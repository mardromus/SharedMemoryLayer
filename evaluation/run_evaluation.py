#!/usr/bin/env python3
"""Evaluation benchmark: isolated agent memory vs. the Shared Memory Layer.

Everything reported here is *measured* against a running deployment through
the public REST API (via the Python SDK). Nothing is simulated inside the
server and no number is hard-coded.

Configurations compared
    isolated  - baseline: every agent can only retrieve memories it created
                itself (per-agent memory silo, ``only_own=true``)
    shared    - proposed: permission-aware semantic search over the shared layer
    lexical   - reference: TF-IDF keyword ranking over exactly the same set of
                memories the agent is authorised to read (isolates the effect of
                semantic embeddings from the effect of sharing)

Metrics
    retrieval accuracy  Hit@1, Hit@k, Precision@k, Recall@k, MRR, nDCG@k
    search precision    fraction of results above the similarity threshold that are relevant
    retrieval latency   client round-trip and server-side time (mean / p50 / p95 / p99)
    knowledge reuse     tasks answered with a relevant memory written by *another* team
    duplicate work      tasks whose knowledge already existed but was not found, so the
                        agent had to re-derive and store it again (and whether the
                        server's duplicate detector flagged the re-stored memory)
    task completion     wall-clock time of the agent runtime (search + synthesis) plus
                        the measured cost of re-deriving/storing when memory did not help
    security            unauthorised access / escalation attempts, blocked vs. leaked,
                        and whether each denial was written to the audit log

Usage (inside the backend container, where ADMIN_* are set):
    docker compose exec backend python /opt/evaluation/run_evaluation.py
From the host:
    pip install ./sdk
    python evaluation/run_evaluation.py --base-url http://localhost:8000 --env-file .env
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import random
import re
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from shared_memory import MemoryAPIError, MemoryClient, NotFoundError, PermissionDeniedError

HERE = Path(__file__).resolve().parent
TEAMS = {"dev": "Development", "qa": "QA", "sec": "Security", "support": "Support"}
CONFIGS = ("isolated", "shared", "lexical")


# --------------------------------------------------------------------------- helpers
def load_env_file(path: str | None) -> None:
    if not path or not Path(path).exists():
        return
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return ordered[lo] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def summarise(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 2),
        "p50": round(pct(values, 50), 2),
        "p95": round(pct(values, 95), 2),
        "p99": round(pct(values, 99), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
    }


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = set("a an the of to in on for and or is are was were be by with at from as it this that we our do does how what which why when who should can".split())


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


class TfIdf:
    """Plain TF-IDF cosine ranking (lexical reference baseline)."""

    def __init__(self, docs: dict[str, str]):
        self.ids = list(docs)
        self.tf = {i: Counter(tokens(docs[i])) for i in self.ids}
        n = max(len(self.ids), 1)
        df = Counter(t for c in self.tf.values() for t in c)
        self.idf = {t: math.log((1 + n) / (1 + d)) + 1 for t, d in df.items()}
        self.norm = {i: math.sqrt(sum((v * self.idf[t]) ** 2 for t, v in c.items())) or 1.0 for i, c in self.tf.items()}

    def rank(self, query: str, k: int) -> list[tuple[str, float]]:
        q = Counter(tokens(query))
        qn = math.sqrt(sum((v * self.idf.get(t, 0)) ** 2 for t, v in q.items())) or 1.0
        scores = []
        for i in self.ids:
            dot = sum(v * self.idf.get(t, 0) * self.tf[i].get(t, 0) * self.idf.get(t, 0) for t, v in q.items())
            scores.append((i, dot / (qn * self.norm[i])))
        scores.sort(key=lambda x: -x[1])
        return [s for s in scores[:k] if s[1] > 0]


def ranking_metrics(ranked: list[str], relevant: set[str], k: int) -> dict:
    top = ranked[:k]
    hits = [1 if r in relevant else 0 for r in top]
    first = next((i for i, h in enumerate(hits) if h), None)
    dcg = sum(h / math.log2(i + 2) for i, h in enumerate(hits))
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(relevant), k)))
    return {
        "hit@1": float(bool(top) and top[0] in relevant),
        f"hit@{k}": float(any(hits)),
        f"precision@{k}": sum(hits) / k,
        f"recall@{k}": sum(hits) / len(relevant) if relevant else 0.0,
        "mrr": 1 / (first + 1) if first is not None else 0.0,
        f"ndcg@{k}": dcg / idcg if idcg else 0.0,
    }


# --------------------------------------------------------------------------- benchmark
class Benchmark:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.k = args.top_k
        self.threshold = args.threshold
        self.dataset = json.loads((HERE / "dataset.json").read_text(encoding="utf-8"))
        self.run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.started = datetime.now(timezone.utc)
        self.admin = MemoryClient(args.base_url, username=os.environ["ADMIN_USERNAME"], password=os.environ["ADMIN_PASSWORD"], timeout=120)
        self.cleanup_memory_ids: list[str] = []
        self.agent_ids: list[str] = []
        self.team_ids: list[str] = []
        self.results: dict = {"run_id": self.run_id}

    # ---------------------------------------------------------------- setup
    def setup(self) -> None:
        log(f"run {self.run_id}: creating isolated evaluation teams, agents and project")
        self.teams = {}
        for key, label in TEAMS.items():
            team = self.admin.request("POST", "/api/teams", json={"name": f"Eval {self.run_id} {label}", "description": "evaluation run"})
            self.teams[key] = team["id"]
            self.team_ids.append(team["id"])
        self.project = self.admin.request("POST", "/api/projects", json={"name": f"eval-{self.run_id}", "description": "evaluation corpus"})
        self.agents: dict[str, MemoryClient] = {}
        self.agent_names: dict[str, str] = {}
        for key in TEAMS:
            agent = self.admin.request("POST", "/api/agents", json={"name": f"eval-{self.run_id}-{key}", "team_id": self.teams[key], "agent_type": "evaluation"})
            self.agent_ids.append(agent["id"])
            self.agent_names[key] = agent["name"]
            self.agents[key] = MemoryClient(self.args.base_url, api_key=agent["api_key"], timeout=120)

    def populate(self) -> None:
        log(f"writing {len(self.dataset['memories'])} labelled memories through the API")
        self.key_to_id: dict[str, str] = {}
        self.id_to_key: dict[str, str] = {}
        self.mem_by_key = {m["key"]: m for m in self.dataset["memories"]}
        write_ms, server_dups = [], 0
        for m in self.dataset["memories"]:
            client = self.agents[m["team"]]
            t0 = time.perf_counter()
            res = client.remember(
                m["title"], m["content"], m["type"], tags=m.get("tags"), visibility=m["visibility"],
                sensitivity=m.get("sensitivity"), project_id=self.project["id"], source="evaluation", source_ref=m["key"],
                allowed_team_ids=[self.teams[t] for t in m.get("share_with", [])] or None,
            )
            write_ms.append((time.perf_counter() - t0) * 1000)
            server_dups += bool(res.warnings.duplicates)
            self.key_to_id[m["key"]] = res.memory.id
            self.id_to_key[res.memory.id] = m["key"]
            self.cleanup_memory_ids.append(res.memory.id)
        self.results["write_latency_ms"] = summarise(write_ms)
        self.results["corpus"] = {
            "memories": len(self.dataset["memories"]),
            "by_team": dict(Counter(m["team"] for m in self.dataset["memories"])),
            "by_visibility": dict(Counter(m["visibility"] for m in self.dataset["memories"])),
            "duplicates_flagged_while_loading": server_dups,
        }

    def readable_corpus(self, team: str) -> dict[str, str]:
        """Memories of the evaluation project that this agent may read (via the permission-aware listing)."""
        page = self.agents[team].request("GET", "/api/memories", params={"project_id": self.project["id"], "page_size": 200})
        docs = {}
        for item in page["items"]:
            full = self.agents[team].get(item["id"])
            docs[item["id"]] = f"{full.title} {full.content} {' '.join(full.tags)}"
        return docs

    # ---------------------------------------------------------------- retrieval
    def retrieval(self) -> None:
        queries = self.dataset["queries"]
        log(f"retrieval: {len(queries)} queries x {self.args.repeat} repeats x (isolated, shared) + lexical")
        lexical = {team: TfIdf(self.readable_corpus(team)) for team in TEAMS}
        per_query, latency = [], {c: {"client_ms": [], "server_ms": []} for c in ("isolated", "shared")}
        for rep in range(self.args.repeat):
            for qi, q in enumerate(queries):
                client = self.agents[q["team"]]
                relevant = {self.key_to_id[k] for k in q["relevant"]}
                row = {"query_id": qi, "team": q["team"], "query": q["text"], "relevant": ";".join(q["relevant"]),
                       "cross_team": all(self.mem_by_key[k]["team"] != q["team"] for k in q["relevant"])}
                for config in ("isolated", "shared"):
                    t0 = time.perf_counter()
                    res = client.search(q["text"], top_k=self.k, project_ids=[self.project["id"]], similarity_threshold=0.0, only_own=config == "isolated")
                    latency[config]["client_ms"].append((time.perf_counter() - t0) * 1000)
                    latency[config]["server_ms"].append(res.took_ms)
                    if rep:
                        continue
                    ranked = [r.memory_id for r in res]
                    above = [r for r in res if r.similarity >= self.threshold]
                    row[config] = {
                        **ranking_metrics(ranked, relevant, self.k),
                        "returned": len(above),
                        "relevant_returned": sum(r.memory_id in relevant for r in above),
                        "top1_similarity": res[0].similarity if len(res) else None,
                        "top1": self.id_to_key.get(ranked[0]) if ranked else None,
                        "reuse": any(r.memory_id in relevant and r.team_id != self.teams[q["team"]] for r in above),
                        "answered": bool(above) and above[0].memory_id in relevant,
                    }
                if rep == 0:
                    ranked = [i for i, _ in lexical[q["team"]].rank(q["text"], self.k)]
                    row["lexical"] = {**ranking_metrics(ranked, relevant, self.k), "top1": self.id_to_key.get(ranked[0]) if ranked else None}
                    per_query.append(row)
        self.per_query = per_query

        summary = {}
        for config in CONFIGS:
            rows = [r[config] for r in per_query]
            metric_names = [m for m in rows[0] if isinstance(rows[0][m], float) and m not in ("top1_similarity",)]
            s = {m: round(statistics.fmean(r[m] for r in rows), 4) for m in metric_names}
            cross = [r[config] for r in per_query if r["cross_team"]]
            s["hit@k_cross_team_queries"] = round(statistics.fmean(r[f"hit@{self.k}"] for r in cross), 4) if cross else None
            if config != "lexical":
                returned = sum(r["returned"] for r in rows)
                s["search_precision_above_threshold"] = round(sum(r["relevant_returned"] for r in rows) / returned, 4) if returned else None
                s["answered_correctly_rate"] = round(statistics.fmean(float(r["answered"]) for r in rows), 4)
                s["knowledge_reuse_rate"] = round(statistics.fmean(float(r["reuse"]) for r in rows), 4)
            summary[config] = s
        self.results["retrieval"] = {
            "queries": len(per_query),
            "cross_team_queries": sum(r["cross_team"] for r in per_query),
            "top_k": self.k,
            "similarity_threshold": self.threshold,
            "metrics": summary,
        }
        self.results["search_latency_ms"] = {c: {"client": summarise(v["client_ms"]), "server": summarise(v["server_ms"])} for c, v in latency.items()}

    # ---------------------------------------------------------------- agent tasks
    def tasks(self) -> None:
        queries = self.dataset["queries"]
        log(f"agent runtime: {len(queries)} tasks x (isolated, shared)")
        outcome = {c: [] for c in ("isolated", "shared")}
        for q in queries:
            relevant = {self.key_to_id[k] for k in q["relevant"]}
            for config in ("isolated", "shared"):
                t0 = time.perf_counter()
                out = self.agents[q["team"]].run_task(
                    q["text"], top_k=self.k, project_id=self.project["id"], similarity_threshold=self.threshold, only_own=config == "isolated"
                )
                ms = (time.perf_counter() - t0) * 1000
                used = [m.memory_id for m in out.memories_used]
                outcome[config].append({
                    "query": q, "runtime_ms": ms, "provider": out.provider, "resolved": bool(set(used) & relevant),
                    "cross_team_used": out.reused_cross_team,
                })

        # Re-derivation phase: an agent that did not find existing knowledge must
        # produce and store it again. Measured per configuration, then removed so
        # the two configurations cannot influence each other.
        summary = {}
        for config, rows in outcome.items():
            created = []
            for row in rows:
                row["resolve_ms"] = 0.0
                row["duplicate_flagged"] = None
                if row["resolved"]:
                    continue
                q = row["query"]
                source = self.mem_by_key[q["relevant"][0]]
                t0 = time.perf_counter()
                res = self.agents[q["team"]].remember(
                    f"Re-derived: {source['title']}", source["content"], source["type"], project_id=self.project["id"],
                    source="evaluation-rederived", tags=["evaluation"],
                )
                row["resolve_ms"] = (time.perf_counter() - t0) * 1000
                known = {self.key_to_id[k] for k in q["relevant"]}
                row["duplicate_flagged"] = any(d.memory_id in known for d in res.warnings.duplicates)
                created.append(res.memory.id)
            for mid in created:
                self.admin.request("DELETE", f"/api/memories/{mid}", params={"permanent": "true"})
            total = [r["runtime_ms"] + r["resolve_ms"] for r in rows]
            redo = [r for r in rows if not r["resolved"]]
            summary[config] = {
                "tasks": len(rows),
                "llm_provider": rows[0]["provider"],
                "resolved_from_memory_rate": round(sum(r["resolved"] for r in rows) / len(rows), 4),
                "duplicate_task_rate": round(len(redo) / len(rows), 4),
                "duplicates_flagged_by_server": sum(bool(r["duplicate_flagged"]) for r in redo),
                "tasks_using_other_teams_memory": sum(r["cross_team_used"] > 0 for r in rows),
                "runtime_ms": summarise([r["runtime_ms"] for r in rows]),
                "task_completion_ms": summarise(total),
                "rederive_and_store_ms": summarise([r["resolve_ms"] for r in redo]),
            }
        self.results["agent_tasks"] = summary

    # ---------------------------------------------------------------- security
    def security(self) -> None:
        log("security: adversarial access attempts by agents from other teams")
        suite_start = datetime.now(timezone.utc)
        attempts = Counter()
        blocked = Counter()
        leaked = []
        protected = [m for m in self.dataset["memories"] if m["visibility"] in ("team", "private", "selected_teams")]
        expected_audited = 0
        for m in protected:
            mid = self.key_to_id[m["key"]]
            authorised = {m["team"], *m.get("share_with", [])}
            for team, client in self.agents.items():
                if team in authorised:
                    continue
                checks = {
                    "direct_read": lambda: client.get(mid),
                    "update": lambda: client.update(mid, content="tampered by evaluation"),
                    "read_versions": lambda: client.versions(mid),
                    "share_to_self": lambda: client.share(mid, team_ids=[self.teams[team]]),
                }
                for name, call in checks.items():
                    attempts[name] += 1
                    try:
                        call()
                        leaked.append({"memory": m["key"], "agent_team": team, "attempt": name})
                    except (PermissionDeniedError, NotFoundError):
                        blocked[name] += 1
                        expected_audited += 1
                for name, query in (("semantic_search_title", m["title"]), ("semantic_search_content", m["content"][:300])):
                    attempts[name] += 1
                    res = client.search(query, top_k=10, project_ids=[self.project["id"]], similarity_threshold=0.0)
                    if any(r.memory_id == mid for r in res):
                        leaked.append({"memory": m["key"], "agent_team": team, "attempt": name})
                    else:
                        blocked[name] += 1

        escalations = {
            "create_team": lambda c: c.request("POST", "/api/teams", json={"name": f"rogue {self.run_id}"}),
            "create_agent": lambda c: c.request("POST", "/api/agents", json={"name": f"rogue-{self.run_id}", "team_id": self.teams["dev"]}),
            "read_audit_log": lambda c: c.request("GET", "/api/audit-logs"),
            "list_users": lambda c: c.request("GET", "/api/users"),
            "reindex_vectors": lambda c: c.request("POST", "/api/admin/reindex"),
        }
        for name, call in escalations.items():
            for client in self.agents.values():
                attempts[f"escalation:{name}"] += 1
                try:
                    call(client)
                    leaked.append({"attempt": f"escalation:{name}"})
                except PermissionDeniedError:
                    blocked[f"escalation:{name}"] += 1
                    expected_audited += 1

        # Were the denials written to the audit log?
        names = set(self.agent_names.values())
        audited, page = 0, 1
        while True:
            data = self.admin.request("GET", "/api/audit-logs", params={"status": "denied", "since": suite_start.isoformat(), "page": page, "page_size": 500})
            audited += sum(1 for e in data["items"] if e["actor_name"] in names)
            if page * 500 >= data["total"]:
                break
            page += 1
        total_attempts = sum(attempts.values())
        self.results["security"] = {
            "protected_memories": len(protected),
            "attempts_by_type": dict(attempts),
            "blocked_by_type": dict(blocked),
            "total_attempts": total_attempts,
            "blocked": sum(blocked.values()),
            "leaked": len(leaked),
            "leak_details": leaked,
            "block_rate": round(sum(blocked.values()) / total_attempts, 4) if total_attempts else None,
            "denials_expected_in_audit_log": expected_audited,
            "denials_found_in_audit_log": audited,
        }

    # ---------------------------------------------------------------- scale
    def scale(self) -> None:
        n = self.args.scale
        if n <= 0:
            return
        checkpoints = sorted({int(c) for c in self.args.scale_checkpoints.split(",") if int(c) <= n} | {n})
        log(f"scale: loading {n} synthetic memories; search latency at checkpoints {checkpoints}")
        rng = random.Random(42)
        subjects = ["checkout service", "billing API", "search index", "auth gateway", "mobile app", "reporting job", "notification queue",
                    "inventory service", "admin console", "data pipeline", "CDN edge", "payment webhook", "image worker", "session store"]
        problems = ["timed out under load", "leaked memory", "returned stale data", "threw a null pointer exception", "exceeded its rate limit",
                    "lost messages during deploys", "had a race condition", "logged sensitive fields", "used too many database connections",
                    "failed schema validation", "slowed down after a dependency upgrade", "crashed on malformed input"]
        fixes = ["added a circuit breaker", "introduced request coalescing", "raised the pool size", "added idempotency keys",
                 "switched to cursor pagination", "added input validation", "scrubbed the log fields", "added a retry with jitter",
                 "moved the work to a background queue", "pinned the dependency version", "added an index", "tuned the cache TTL"]
        team = self.admin.request("POST", "/api/teams", json={"name": f"Eval {self.run_id} Scale", "description": "scale test"})
        self.team_ids.append(team["id"])
        project = self.admin.request("POST", "/api/projects", json={"name": f"eval-{self.run_id}-scale"})
        loaders: list[MemoryClient] = []
        for i in range(math.ceil(n / 100)):
            agent = self.admin.request("POST", "/api/agents", json={"name": f"eval-{self.run_id}-loader-{i}", "team_id": team["id"]})
            self.agent_ids.append(agent["id"])
            loaders.append(MemoryClient(self.args.base_url, api_key=agent["api_key"], timeout=120))
        search_client = loaders[0]
        queries = [f"{rng.choice(subjects)} {rng.choice(problems)}" for _ in range(40)]
        points, write_ms, written = [], [], 0
        for cp in checkpoints:
            while written < cp:
                s, p, f = rng.choice(subjects), rng.choice(problems), rng.choice(fixes)
                t0 = time.perf_counter()
                res = loaders[written // 100].remember(
                    f"The {s} {p} (case {written})", f"Incident {written}: the {s} {p}. The team {f}, which resolved the issue.",
                    "incident", visibility="organization", project_id=project["id"], source="evaluation-scale",
                )
                write_ms.append((time.perf_counter() - t0) * 1000)
                self.cleanup_memory_ids.append(res.memory.id)
                written += 1
            client_ms, server_ms = [], []
            for q in queries:
                t0 = time.perf_counter()
                res = search_client.search(q, top_k=self.k, project_ids=[project["id"]], similarity_threshold=0.0)
                client_ms.append((time.perf_counter() - t0) * 1000)
                server_ms.append(res.took_ms)
            points.append({"corpus_size": cp, "client_ms": summarise(client_ms), "server_ms": summarise(server_ms)})
            log(f"  {cp:>6} memories: search p50 {points[-1]['client_ms']['p50']} ms, p95 {points[-1]['client_ms']['p95']} ms")
        self.results["scale"] = {"write_latency_ms": summarise(write_ms), "points": points}

    # ---------------------------------------------------------------- cleanup & report
    def cleanup(self) -> None:
        if self.args.keep:
            log("--keep given: evaluation data left in place")
            return
        log(f"cleanup: permanently deleting {len(self.cleanup_memory_ids)} evaluation memories, agents and teams")
        for mid in self.cleanup_memory_ids:
            try:
                self.admin.request("DELETE", f"/api/memories/{mid}", params={"permanent": "true"})
            except MemoryAPIError:
                pass
        for aid in self.agent_ids:
            try:
                self.admin.request("DELETE", f"/api/agents/{aid}")
            except MemoryAPIError:
                pass
        for tid in self.team_ids:
            try:
                self.admin.request("PATCH", f"/api/teams/{tid}", json={"is_active": False})
            except MemoryAPIError:
                pass

    def write_report(self) -> Path:
        health = self.admin.request("GET", "/health")
        self.results["environment"] = {
            "started_at": self.started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "base_url": self.args.base_url,
            "server_version": health["version"],
            "embedding_model": health["components"]["embedding_model"]["model"],
            "llm_provider": health["components"]["llm"]["provider"],
            "client_python": platform.python_version(),
            "client_platform": platform.platform(),
            "repeat": self.args.repeat,
        }
        out = Path(self.args.out) / self.run_id
        try:
            out.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            out = Path("/tmp/evaluation-results") / self.run_id
            out.mkdir(parents=True, exist_ok=True)
            log(f"results directory not writable; using {out} (copy it out with docker compose cp)")
        (out / "results.json").write_text(json.dumps(self.results, indent=2, default=str), encoding="utf-8")
        with (out / "per_query.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["query_id", "team", "query", "relevant", "cross_team",
                             *[f"{c}_{m}" for c in CONFIGS for m in ("top1", "hit@1", f"hit@{self.k}", "mrr")]])
            for r in self.per_query:
                writer.writerow([r["query_id"], r["team"], r["query"], r["relevant"], r["cross_team"],
                                 *[r[c].get(m) for c in CONFIGS for m in ("top1", "hit@1", f"hit@{self.k}", "mrr")]])
        (out / "results.md").write_text(self.markdown(), encoding="utf-8")
        return out

    def markdown(self) -> str:
        r, k = self.results, self.k
        m = r["retrieval"]["metrics"]
        env = r["environment"]
        fmt = lambda v, p=3: "—" if v is None else (f"{v:.{p}f}" if isinstance(v, float) else str(v))
        lines = [
            f"# Evaluation results — run {self.run_id}",
            "",
            f"*Measured against `{env['base_url']}` (server {env['server_version']}, embeddings `{env['embedding_model']}`, "
            f"LLM provider `{env['llm_provider']}`) on {env['started_at'][:19]} UTC. Client: Python {env['client_python']}.*",
            "",
            "## Setup",
            "",
            f"- Corpus: **{r['corpus']['memories']} labelled memories** written by 4 team agents "
            f"({', '.join(f'{t}: {c}' for t, c in r['corpus']['by_team'].items())}); visibility {r['corpus']['by_visibility']}.",
            f"- Queries: **{r['retrieval']['queries']}** paraphrased questions, {r['retrieval']['cross_team_queries']} of which can only be answered "
            f"with knowledge owned by another team.",
            f"- Top-k = {k}; similarity threshold for \"returned\" results = {self.threshold}; latency repeats = {env['repeat']}.",
            "",
            "## Retrieval quality",
            "",
            "| Metric | Isolated memory (baseline) | Shared memory (proposed) | Lexical TF-IDF on shared set |",
            "|---|---|---|---|",
        ]
        for name in ["hit@1", f"hit@{k}", f"precision@{k}", f"recall@{k}", "mrr", f"ndcg@{k}", "hit@k_cross_team_queries",
                     "search_precision_above_threshold", "answered_correctly_rate", "knowledge_reuse_rate"]:
            lines.append(f"| {name} | {fmt(m['isolated'].get(name))} | {fmt(m['shared'].get(name))} | {fmt(m['lexical'].get(name))} |")
        lat = r["search_latency_ms"]
        lines += [
            "",
            "## Retrieval latency (ms)",
            "",
            "| Configuration | Side | n | mean | p50 | p95 | p99 |",
            "|---|---|---|---|---|---|---|",
        ]
        for c in ("isolated", "shared"):
            for side in ("client", "server"):
                s = lat[c][side]
                lines.append(f"| {c} | {side} | {s['n']} | {s['mean']} | {s['p50']} | {s['p95']} | {s['p99']} |")
        w = r["write_latency_ms"]
        lines += ["", f"Memory creation (validate + embed + duplicate/conflict detection + PostgreSQL + Qdrant + version + audit): "
                  f"mean {w['mean']} ms, p50 {w['p50']} ms, p95 {w['p95']} ms (n={w['n']}).", ""]
        t = r["agent_tasks"]
        lines += [
            "## Agent tasks: reuse, duplicate work and completion time",
            "",
            "| Metric | Isolated memory | Shared memory |",
            "|---|---|---|",
            f"| Tasks resolved from existing memory | {fmt(t['isolated']['resolved_from_memory_rate'])} | {fmt(t['shared']['resolved_from_memory_rate'])} |",
            f"| Duplicate task rate (knowledge existed but was re-derived) | {fmt(t['isolated']['duplicate_task_rate'])} | {fmt(t['shared']['duplicate_task_rate'])} |",
            f"| Re-stored memories flagged as duplicates by the server | {t['isolated']['duplicates_flagged_by_server']} | {t['shared']['duplicates_flagged_by_server']} |",
            f"| Tasks that used another team's memory | {t['isolated']['tasks_using_other_teams_memory']} | {t['shared']['tasks_using_other_teams_memory']} |",
            f"| Agent runtime per task, mean / p95 (ms) | {t['isolated']['runtime_ms']['mean']} / {t['isolated']['runtime_ms']['p95']} | {t['shared']['runtime_ms']['mean']} / {t['shared']['runtime_ms']['p95']} |",
            f"| Task completion incl. re-derivation, mean / p95 (ms) | {t['isolated']['task_completion_ms']['mean']} / {t['isolated']['task_completion_ms']['p95']} | {t['shared']['task_completion_ms']['mean']} / {t['shared']['task_completion_ms']['p95']} |",
            "",
            f"LLM provider during the run: `{t['shared']['llm_provider']}`. Re-derivation is measured as one extra store of the "
            "re-derived knowledge (embedding, duplicate detection, persistence); real re-derivation by an LLM or engineer is far more "
            "expensive, so the completion-time gap is a lower bound.",
            "",
        ]
        s = r["security"]
        lines += [
            "## Security: unauthorised access attempts",
            "",
            f"{s['protected_memories']} protected memories (team/private/selected-teams); every agent outside the authorised teams attempted each access path.",
            "",
            "| Attempt | Attempts | Blocked |",
            "|---|---|---|",
        ]
        for name, count in s["attempts_by_type"].items():
            lines.append(f"| {name} | {count} | {s['blocked_by_type'].get(name, 0)} |")
        lines += [
            f"| **Total** | **{s['total_attempts']}** | **{s['blocked']}** |",
            "",
            f"Leaked: **{s['leaked']}** · block rate **{fmt(s['block_rate'], 4)}** · denials written to the audit log: "
            f"**{s['denials_found_in_audit_log']} / {s['denials_expected_in_audit_log']}** expected.",
            "",
        ]
        if "scale" in r:
            lines += ["## Search latency vs. corpus size", "", "| Memories in project | client p50 | client p95 | server p50 | server p95 |", "|---|---|---|---|---|"]
            for p in r["scale"]["points"]:
                lines.append(f"| {p['corpus_size']} | {p['client_ms']['p50']} | {p['client_ms']['p95']} | {p['server_ms']['p50']} | {p['server_ms']['p95']} |")
            lines += ["", f"Bulk write latency during the scale test: mean {r['scale']['write_latency_ms']['mean']} ms (n={r['scale']['write_latency_ms']['n']}).", ""]
        lines += [
            "## Method notes",
            "",
            "- *Isolated* restricts retrieval to memories the asking agent created itself (`only_own=true`), modelling per-agent memory silos.",
            "- *Lexical* ranks exactly the memories the agent may read with TF-IDF cosine similarity, so the difference between *lexical* and *shared* "
            "isolates the contribution of semantic embeddings.",
            "- Ranking metrics use the top-k list irrespective of threshold; *search precision* and *answered correctly* only count results at or above the threshold.",
            "- Query texts are paraphrases written for this benchmark; the corpus is small and synthetic, so absolute numbers should not be generalised.",
        ]
        return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Shared Memory Layer evaluation benchmark")
    parser.add_argument("--base-url", default=os.environ.get("SML_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--env-file", default=None, help="read ADMIN_USERNAME / ADMIN_PASSWORD from this .env file")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--repeat", type=int, default=3, help="repetitions of the query set for latency statistics")
    parser.add_argument("--scale", type=int, default=0, help="additionally load N synthetic memories and measure latency vs size")
    parser.add_argument("--scale-checkpoints", default="100,250,500,1000,2000")
    parser.add_argument("--out", default=str(HERE / "results"))
    parser.add_argument("--keep", action="store_true", help="do not delete the evaluation data afterwards")
    args = parser.parse_args()
    load_env_file(args.env_file)
    if not os.environ.get("ADMIN_USERNAME") or not os.environ.get("ADMIN_PASSWORD"):
        print("ADMIN_USERNAME and ADMIN_PASSWORD must be set (or pass --env-file .env)", file=sys.stderr)
        return 2

    bench = Benchmark(args)
    try:
        bench.setup()
        bench.populate()
        bench.retrieval()
        bench.tasks()
        bench.security()
        bench.scale()
        out = bench.write_report()
    finally:
        bench.cleanup()
    log(f"results written to {out}")
    print()
    print((out / "results.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
