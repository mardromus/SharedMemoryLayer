#!/usr/bin/env python3
"""LoCoMo retrieval benchmark — compares the Shared Memory Layer with standard baselines
and with the numbers other memory systems report on the same dataset.

LoCoMo (Maharana et al., ACL 2024) is the benchmark most agent-memory systems
(Mem0, Zep, LangMem, A-Mem, MemGPT/Letta, OpenAI memory) report on. Each of the
10 released conversations has ~300 turns over ~19 sessions and a set of
questions whose *evidence* turns are annotated (e.g. "D3:7").

What is measured here (all live, through the public API):
  * every dialog turn is stored as one memory (bulk import, one agent per conversation)
  * every answerable question (categories 1-4; category 5 is adversarial and has no
    answer in the conversation) is issued as a search with k = 50
  * evidence-retrieval quality: Recall@k (fraction of evidence turns retrieved — the
    metric the LoCoMo paper reports for its RAG retriever), Hit@k, MRR, nDCG@10
  * per-category Recall@10, search latency (client & server), retrieved context size

Configurations:
  hybrid / semantic / keyword   the three search modes of this system
  bm25                          classic lexical baseline over the same turns
  recency                       last-k turns (what a context-window-only agent sees)
  dense                         a plain vector store: all-MiniLM-L6-v2 + cosine over the same turns

End-to-end answer accuracy (LLM-as-a-Judge "J") needs a strong answering and judging
LLM (the published comparisons use GPT-4o-mini); it is *not* computed here.

Usage (inside the backend container):
    docker compose exec backend python /opt/evaluation/locomo_benchmark.py
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from shared_memory import MemoryClient, MemoryAPIError, RateLimitError

from bench_common import DEFAULT_DENSE_BASELINE, load_dense_baseline

HERE = Path(__file__).resolve().parent
DATA_URL = "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"
# Category ids in the released data (proportions match the paper's table: 4 is the largest = single-hop)
CATEGORIES = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop", 5: "adversarial"}
KS = (1, 5, 10, 25, 50)
DIA = re.compile(r"D\d+:\d+")
TOKEN = re.compile(r"[a-z0-9]+")
STOP = set("a an the of to in on for and or is are was were be by with at from as it this that do does did how what which why when who whom where "
           "i you he she they we me my your his her their our its has have had will would can could should".split())

# Published reference numbers (copied from the cited papers; different setups — see notes)
PUBLISHED_RECALL = {  # LoCoMo paper, Table 3: DRAGON retriever over dialog turns (full 50-conversation benchmark)
    5: 58.8, 10: 67.5, 25: 79.9, 50: 84.8,
}
PUBLISHED_SYSTEMS = [  # Mem0 paper (Chhikara et al., 2025), Table 2 — LoCoMo, GPT-4o-mini answers + judge
    # name, overall J (%), search p50 (s), search p95 (s), memory tokens per conversation
    ("Full-context (no memory layer)", 72.90, None, None, 26031),
    ("Mem0g (graph)", 68.44, 0.476, 0.657, 3616),
    ("Mem0", 66.88, 0.148, 0.200, 1764),
    ("Zep", 65.99, 0.513, 0.778, 3911),
    ("LangMem", 58.10, 17.99, 59.82, 127),
    ("OpenAI memory", 52.90, None, None, 4437),
    ("A-Mem", 48.38, 0.668, 1.485, 2520),
    ("Best RAG (k=2, 256-token chunks)", 60.97, 0.255, 0.699, None),
]


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    k = (len(ordered) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return ordered[lo] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def tokens(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOP]


class BM25:
    def __init__(self, docs: dict[str, str], k1: float = 1.5, b: float = 0.75):
        self.ids = list(docs)
        self.tf = {i: Counter(tokens(docs[i])) for i in self.ids}
        self.len = {i: sum(c.values()) for i, c in self.tf.items()}
        self.avg = statistics.fmean(self.len.values()) if self.len else 1.0
        n = len(self.ids)
        df = Counter(t for c in self.tf.values() for t in c)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        self.k1, self.b = k1, b

    def rank(self, query: str, k: int) -> list[str]:
        q = tokens(query)
        scores = []
        for i in self.ids:
            tf, dl, s = self.tf[i], self.len[i], 0.0
            for t in q:
                if t in tf:
                    s += self.idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * dl / self.avg))
            if s > 0:
                scores.append((s, i))
        scores.sort(reverse=True)
        return [i for _, i in scores[:k]]


def metrics(ranked: list[str], relevant: set[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for k in KS:
        top = ranked[:k]
        found = len(relevant & set(top))
        out[f"recall@{k}"] = found / len(relevant)
        out[f"hit@{k}"] = float(found > 0)
    first = next((i for i, r in enumerate(ranked) if r in relevant), None)
    out["mrr"] = 1 / (first + 1) if first is not None else 0.0
    dcg = sum(1 / math.log2(i + 2) for i, r in enumerate(ranked[:10]) if r in relevant)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(relevant), 10)))
    out["ndcg@10"] = dcg / idcg if idcg else 0.0
    return out


def load_dataset(path: Path) -> list[dict]:
    if not path.exists():
        log(f"downloading LoCoMo from {DATA_URL}")
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(DATA_URL, timeout=120) as r:  # noqa: S310 - fixed https URL
            path.write_bytes(r.read())
    return json.loads(path.read_text(encoding="utf-8"))


def turns_of(sample: dict) -> list[dict]:
    conv = sample["conversation"]
    out = []
    n = 1
    while f"session_{n}" in conv:
        date = conv.get(f"session_{n}_date_time", "")
        for turn in conv[f"session_{n}"]:
            text = turn.get("text", "")
            if turn.get("blip_caption"):
                text += f" [shares a photo: {turn['blip_caption']}]"
            out.append({"dia_id": turn["dia_id"], "session": n, "date": date, "speaker": turn["speaker"], "text": text})
        n += 1
    return out


class Bench:
    def __init__(self, args):
        self.args = args
        self.run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.admin = MemoryClient(args.base_url, username=os.environ["ADMIN_USERNAME"], password=os.environ["ADMIN_PASSWORD"], timeout=300)
        self.created_agents: list[str] = []
        self.created_memories: list[str] = []
        self.team_id: str | None = None

    def search(self, client: MemoryClient, **kw):
        while True:
            t0 = time.perf_counter()
            try:
                res = client.search(**kw)
                return res, (time.perf_counter() - t0) * 1000
            except RateLimitError as exc:  # wait outside the timed region
                time.sleep(max(exc.retry_after or 1, 1))

    def run(self) -> Path:
        data = load_dataset(Path(self.args.dataset))[: self.args.conversations]
        team = self.admin.request("POST", "/api/teams", json={"name": f"LoCoMo {self.run_id}", "description": "LoCoMo benchmark"})
        self.team_id = team["id"]
        dense = load_dense_baseline(self.args.dense_baseline)
        configs = ["hybrid", "semantic", "keyword", "bm25", "recency"] + (["dense"] if dense else [])
        per_q: list[dict] = []
        latency = {c: {"client": [], "server": []} for c in ("hybrid", "semantic", "keyword")}
        ingest_ms, n_turns, conv_chars, ctx_chars = [], 0, [], defaultdict(list)
        category_counts = Counter()

        for si, sample in enumerate(data):
            sid = sample.get("sample_id", f"conv-{si}")
            turns = turns_of(sample)
            n_turns += len(turns)
            conv_chars.append(sum(len(t["text"]) + len(t["speaker"]) + 2 for t in turns))
            agent = self.admin.request("POST", "/api/agents", json={"name": f"locomo-{self.run_id}-{si}", "team_id": self.team_id, "agent_type": "evaluation"})
            self.created_agents.append(agent["id"])
            client = MemoryClient(self.args.base_url, api_key=agent["api_key"], timeout=300, max_retries=0)
            project = self.admin.request("POST", "/api/projects", json={"name": f"locomo-{self.run_id}-{sid}"[:120], "team_id": self.team_id})
            items = [{
                "title": f"{t['speaker']} — session {t['session']} ({t['date']})"[:200],
                "content": f"[{t['date']}] {t['speaker']}: {t['text']}",
                "memory_type": "context", "visibility": "team", "project_id": project["id"],
                "source": "locomo", "source_ref": t["dia_id"], "tags": ["locomo", f"session-{t['session']}"],
            } for t in turns]
            t0 = time.perf_counter()
            res = MemoryClient(self.args.base_url, api_key=agent["api_key"], timeout=300).remember_many(items, detect_duplicates=False)
            ingest_ms.append((time.perf_counter() - t0) * 1000)
            if res["failed"]:
                raise SystemExit(f"ingestion failed for {res['failed']} turns: {[r for r in res['results'] if r['status'] == 'error'][:3]}")
            id_to_dia = {r["memory_id"]: turns[r["index"]]["dia_id"] for r in res["results"]}
            self.created_memories.extend(id_to_dia)
            dia_order = [t["dia_id"] for t in turns]
            docs = {t["dia_id"]: f"{t['speaker']} {t['date']} {t['text']}" for t in turns}
            bm25 = BM25(docs)
            dense_index = dense.index({t["dia_id"]: f"[{t['date']}] {t['speaker']}: {t['text']}" for t in turns}) if dense else None
            texts = {t["dia_id"]: t["text"] for t in turns}

            questions = [q for q in sample["qa"] if q.get("category") in (1, 2, 3, 4)]
            log(f"conversation {si + 1}/{len(data)} ({sid}): {len(turns)} turns ingested in {ingest_ms[-1] / 1000:.1f}s, {len(questions)} questions")
            for q in questions:
                evidence = set()
                for e in q.get("evidence", []) or []:
                    evidence.update(DIA.findall(str(e)))
                evidence &= set(dia_order)
                if not evidence:
                    continue
                cat = CATEGORIES[q["category"]]
                category_counts[cat] += 1
                row = {"conversation": sid, "category": cat, "question": q["question"], "evidence": ";".join(sorted(evidence))}
                for mode in ("hybrid", "semantic", "keyword"):
                    r, ms = self.search(client, query=q["question"], top_k=50, project_ids=[project["id"]], similarity_threshold=0.0, mode=mode)
                    latency[mode]["client"].append(ms)
                    latency[mode]["server"].append(r.took_ms)
                    ranked = [id_to_dia.get(x.memory_id) for x in r]
                    ranked = [d for d in ranked if d]
                    row[mode] = metrics(ranked, evidence)
                    if mode == "hybrid":
                        ctx_chars["hybrid@10"].append(sum(len(texts[d]) for d in ranked[:10]))
                row["bm25"] = metrics(bm25.rank(q["question"], 50), evidence)
                row["recency"] = metrics(list(reversed(dia_order))[:50], evidence)
                if dense_index:
                    row["dense"] = metrics(dense_index.rank(q["question"], 50), evidence)
                per_q.append(row)

        summary = {}
        for c in configs:
            s = {m: round(statistics.fmean(r[c][m] for r in per_q), 4) for m in per_q[0][c]}
            s["recall@10_by_category"] = {cat: round(statistics.fmean(r[c]["recall@10"] for r in per_q if r["category"] == cat), 4)
                                          for cat in sorted(category_counts)}
            summary[c] = s
        lat = {m: {side: {"p50": round(pct(v, 50), 1), "p95": round(pct(v, 95), 1), "mean": round(statistics.fmean(v), 1), "n": len(v)}
                   for side, v in d.items()} for m, d in latency.items()}
        results = {
            "run_id": self.run_id,
            "dataset": {"name": "LoCoMo (locomo10.json)", "conversations": len(data), "turns": n_turns, "questions_evaluated": len(per_q),
                        "questions_by_category": dict(category_counts), "avg_conversation_tokens_approx": round(statistics.fmean(conv_chars) / 4)},
            "retrieval": summary,
            "search_latency_ms": lat,
            "ingestion": {"turns_per_second": round(n_turns / (sum(ingest_ms) / 1000), 1), "total_seconds": round(sum(ingest_ms) / 1000, 1)},
            "context": {"hybrid_top10_tokens_approx": round(statistics.fmean(ctx_chars["hybrid@10"]) / 4)},
            "published_reference": {"locomo_dragon_dialog_recall": PUBLISHED_RECALL, "mem0_paper_table2": PUBLISHED_SYSTEMS},
            "baselines": {"dense_model": dense.model_name if dense else None},
            "environment": {"base_url": self.args.base_url, "finished_at": datetime.now(timezone.utc).isoformat(),
                            "server": self.admin.request("GET", "/health")["components"]["embedding_model"]},
        }
        out = Path(self.args.out) / f"locomo-{self.run_id}"
        try:
            out.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            out = Path("/tmp/evaluation-results") / f"locomo-{self.run_id}"
            out.mkdir(parents=True, exist_ok=True)
        (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        with (out / "per_question.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["conversation", "category", "question", "evidence", *[f"{c}_recall@10" for c in configs], *[f"{c}_mrr" for c in configs]])
            for r in per_q:
                w.writerow([r["conversation"], r["category"], r["question"], r["evidence"], *[round(r[c]["recall@10"], 3) for c in configs], *[round(r[c]["mrr"], 3) for c in configs]])
        (out / "results.md").write_text(self.markdown(results), encoding="utf-8")
        return out

    def markdown(self, r: dict) -> str:
        s, lat, ds = r["retrieval"], r["search_latency_ms"], r["dataset"]
        names = {"hybrid": "**This system — hybrid**", "semantic": "This system — semantic only", "keyword": "This system — keyword only (AND)",
                 "bm25": "BM25 baseline", "recency": "Recency baseline (last-k turns)", "dense": "Plain vector store (all-MiniLM-L6-v2, cosine)"}
        shown = [c for c in ("hybrid", "semantic", "keyword", "bm25", "dense", "recency") if c in s]
        lines = [
            f"# LoCoMo retrieval benchmark — run {r['run_id']}", "",
            f"{ds['conversations']} conversations, {ds['turns']} dialog turns stored as memories, {ds['questions_evaluated']} answerable questions "
            f"({', '.join(f'{k}: {v}' for k, v in ds['questions_by_category'].items())}). Embeddings: `{r['environment']['server']['model']}`.", "",
            "## Evidence retrieval (higher is better)", "",
            "| Configuration | Recall@5 | Recall@10 | Recall@25 | Recall@50 | Hit@1 | Hit@10 | MRR | nDCG@10 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for c in shown:
            m = s[c]
            lines.append(f"| {names[c]} | {m['recall@5']:.3f} | {m['recall@10']:.3f} | {m['recall@25']:.3f} | {m['recall@50']:.3f} | {m['hit@1']:.3f} | {m['hit@10']:.3f} | {m['mrr']:.3f} | {m['ndcg@10']:.3f} |")
        pr = PUBLISHED_RECALL
        lines.append(f"| *Published: LoCoMo paper, DRAGON retriever over dialogs†* | {pr[5] / 100:.3f} | {pr[10] / 100:.3f} | {pr[25] / 100:.3f} | {pr[50] / 100:.3f} | — | — | — | — |")
        cats = sorted(s["hybrid"]["recall@10_by_category"])
        lines += ["", "## Recall@10 by question category", "", "| Configuration | " + " | ".join(cats) + " |", "|---|" + "---|" * len(cats)]
        for c in shown:
            lines.append(f"| {names[c]} | " + " | ".join(f"{s[c]['recall@10_by_category'][k]:.3f}" for k in cats) + " |")
        lines += ["", "## Latency and cost", "",
                  "| Mode | search p50 (client) | search p95 (client) | search p50 (server) | search p95 (server) |", "|---|---|---|---|---|"]
        for m in ("hybrid", "semantic", "keyword"):
            lines.append(f"| {m} | {lat[m]['client']['p50']} ms | {lat[m]['client']['p95']} ms | {lat[m]['server']['p50']} ms | {lat[m]['server']['p95']} ms |")
        lines += ["", f"Ingestion: {r['ingestion']['turns_per_second']} turns/s (bulk import, duplicate detection off). "
                  f"Top-10 hybrid context ≈ {r['context']['hybrid_top10_tokens_approx']} tokens vs ≈ {ds['avg_conversation_tokens_approx']} tokens for a whole conversation "
                  "(≈ 4 characters per token).", "",
                  "## Published results of other memory layers on LoCoMo (reported by their authors — not re-run here)", "",
                  "| System | Overall J (LLM judge) | Search p50 | Search p95 | Memory tokens (as reported) |", "|---|---|---|---|---|"]
        for name, j, p50, p95, tok in PUBLISHED_SYSTEMS:
            f = lambda v, unit="s": "—" if v is None else f"{v}{unit}"  # noqa: E731
            lines.append(f"| {name} | {j}% | {f(p50)} | {f(p95)} | {tok if tok else '—'} |")
        lines += ["",
                  f"This system (measured above): search p50 {lat['hybrid']['client']['p50'] / 1000:.3f}s, p95 {lat['hybrid']['client']['p95'] / 1000:.3f}s (hybrid, k=50, same machine as the database); "
                  f"retrieved context ≈ {r['context']['hybrid_top10_tokens_approx']} tokens at k=10. J score not measured (needs an answering + judging LLM).", "",
                  "## How to read this", "",
                  "* † The LoCoMo paper evaluated DRAGON on its full 50-conversation set; this run uses the 10 publicly released conversations "
                  "(`locomo10.json`) — the same subset the Mem0 paper used. Recall@k = fraction of annotated evidence turns retrieved.",
                  "* The Mem0-paper rows measure *answer accuracy* with GPT-4o-mini as answerer and judge, and latency against hosted services "
                  "over the network. They are **not directly comparable** to retrieval recall; they are listed so the evaluated dimensions line up "
                  "(accuracy, search latency, context size). Zep separately reports 94.8% vs MemGPT 93.4% on the DMR benchmark (Rasmussen et al., 2025).",
                  "* Category 5 (adversarial) questions are excluded, as in the Mem0 evaluation, because their answers are not in the conversation.",
                  "* Keyword-only mode requires every query term to match (full-text AND), so it is precise but misses paraphrases; hybrid combines it with embeddings."]
        return "\n".join(lines) + "\n"

    def cleanup(self) -> None:
        if self.args.keep:
            return
        log(f"cleanup: deleting {len(self.created_memories)} benchmark memories and {len(self.created_agents)} agents")
        for mid in self.created_memories:
            try:
                self.admin.request("DELETE", f"/api/memories/{mid}", params={"permanent": "true"})
            except MemoryAPIError:
                pass
        for aid in self.created_agents:
            try:
                self.admin.request("DELETE", f"/api/agents/{aid}")
            except MemoryAPIError:
                pass
        if self.team_id:
            self.admin.request("PATCH", f"/api/teams/{self.team_id}", json={"is_active": False})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=os.environ.get("SML_BASE_URL", "http://localhost:8000"))
    ap.add_argument("--dataset", default=str(HERE / "datasets" / "locomo10.json"))
    ap.add_argument("--conversations", type=int, default=10)
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--dense-baseline", default=DEFAULT_DENSE_BASELINE, help="fastembed model for the plain-vector-store baseline ('' to skip)")
    args = ap.parse_args()
    if not os.environ.get("ADMIN_USERNAME") or not os.environ.get("ADMIN_PASSWORD"):
        print("ADMIN_USERNAME / ADMIN_PASSWORD must be set", file=sys.stderr)
        return 2
    bench = Bench(args)
    try:
        out = bench.run()
    finally:
        bench.cleanup()
    log(f"results written to {out}")
    print((out / "results.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
