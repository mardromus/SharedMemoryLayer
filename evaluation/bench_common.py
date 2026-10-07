"""Shared helpers for the public-benchmark scripts (LoCoMo, LongMemEval, BEIR).

Everything that touches the Shared Memory Layer goes through the public REST API with the Python SDK:
memories are created with the bulk-import endpoint and retrieved with /api/memories/search, exactly
as an agent would. The baselines (BM25, a plain vector store) run locally over the *same* texts.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import statistics
import time
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from shared_memory import MemoryAPIError, MemoryClient, RateLimitError

TOKEN = re.compile(r"[a-z0-9]+")
STOP = set("a an the of to in on for and or is are was were be by with at from as it this that do does did how what which why when who whom where "
           "i you he she they we me my your his her their our its has have had will would can could should".split())
# The embedding model several vector-store memories use by default (e.g. Chroma's default embedding
# function); run locally as the "plain vector store" baseline.
DEFAULT_DENSE_BASELINE = "sentence-transformers/all-MiniLM-L6-v2"


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    k = (len(ordered) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return ordered[lo] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def latency_summary(values: list[float]) -> dict:
    return {"p50": round(pct(values, 50), 1), "p95": round(pct(values, 95), 1), "mean": round(statistics.fmean(values), 1), "n": len(values)}


def tokens(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOP]


def download(url: str, path: Path) -> Path:
    if not path.exists():
        log(f"downloading {url}")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        with urllib.request.urlopen(url, timeout=600) as r, tmp.open("wb") as fh:  # noqa: S310 - fixed https URLs
            while chunk := r.read(1 << 20):
                fh.write(chunk)
        tmp.rename(path)
    return path


class BM25:
    """Okapi BM25 (k1 = 1.5, b = 0.75) with an inverted index."""

    def __init__(self, docs: dict[str, str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        lengths, self.postings = {}, defaultdict(list)
        for doc_id, text in docs.items():
            tf = Counter(tokens(text))
            lengths[doc_id] = sum(tf.values())
            for t, c in tf.items():
                self.postings[t].append((doc_id, c))
        self.len = lengths
        self.avg = statistics.fmean(lengths.values()) if lengths else 1.0
        n = len(lengths)
        self.idf = {t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()}

    def rank(self, query: str, k: int) -> list[str]:
        scores: dict[str, float] = defaultdict(float)
        for t in set(tokens(query)):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for doc_id, tf in self.postings[t]:
                norm = self.k1 * (1 - self.b + self.b * self.len[doc_id] / self.avg)
                scores[doc_id] += idf * tf * (self.k1 + 1) / (tf + norm)
        return [d for d, _ in sorted(scores.items(), key=lambda x: -x[1])[:k]]


class DenseBaseline:
    """A plain vector store: embed every text with a general-purpose model, rank by cosine similarity."""

    def __init__(self, model_name: str = DEFAULT_DENSE_BASELINE):
        import numpy as np
        from fastembed import TextEmbedding

        self.np = np
        self.model_name = model_name
        self.model = TextEmbedding(model_name=model_name, cache_dir=os.environ.get("BASELINE_MODEL_CACHE", "/tmp/fastembed-baselines"))
        self.cache: dict[str, object] = {}

    def _vectors(self, texts: list[str]):
        keys = [hashlib.sha1(t.encode()).hexdigest() for t in texts]
        missing = [(k, t) for k, t in zip(keys, texts) if k not in self.cache]
        if missing:
            uniq = dict(missing)
            for k, v in zip(uniq, self.model.embed(list(uniq.values()), batch_size=64)):
                self.cache[k] = v / (self.np.linalg.norm(v) or 1.0)
        return self.np.stack([self.cache[k] for k in keys])

    def index(self, docs: dict[str, str]) -> "DenseIndex":
        ids = list(docs)
        return DenseIndex(self, ids, self._vectors([docs[i] for i in ids]))


class DenseIndex:
    def __init__(self, base: DenseBaseline, ids: list[str], matrix):
        self.base, self.ids, self.matrix = base, ids, matrix

    def rank(self, query: str, k: int) -> list[str]:
        q = self.base._vectors([query])[0]
        scores = self.matrix @ q
        top = self.base.np.argsort(-scores)[:k]
        return [self.ids[i] for i in top]


def load_dense_baseline(model_name: str | None) -> DenseBaseline | None:
    if not model_name:
        return None
    try:
        return DenseBaseline(model_name)
    except Exception as exc:  # the baseline is optional: never fail the benchmark because of it
        log(f"dense baseline {model_name} unavailable ({exc}); set HF_HUB_OFFLINE=0 to allow the model download")
        return None


class Harness:
    """Creates an isolated team/agent/projects through the API, ingests in parallel, searches, cleans up."""

    def __init__(self, base_url: str, label: str, workers: int = 4):
        self.base_url = base_url
        self.run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.workers = workers
        self.admin = MemoryClient(base_url, username=os.environ["ADMIN_USERNAME"], password=os.environ["ADMIN_PASSWORD"], timeout=600)
        team = self.admin.request("POST", "/api/teams", json={"name": f"{label} {self.run_id}", "description": f"{label} benchmark"})
        self.team_id = team["id"]
        self.created_agents: list[str] = []
        self.created_memories: list[str] = []
        self.pool = ThreadPoolExecutor(max_workers=workers)

    def agent(self, name: str) -> MemoryClient:
        agent = self.admin.request("POST", "/api/agents", json={"name": name[:100], "team_id": self.team_id, "agent_type": "evaluation"})
        self.created_agents.append(agent["id"])
        return MemoryClient(self.base_url, api_key=agent["api_key"], timeout=600, max_retries=0)

    def project(self, name: str) -> str:
        return self.admin.request("POST", "/api/projects", json={"name": name[:120], "team_id": self.team_id})["id"]

    def _bulk(self, client: MemoryClient, chunk: list[dict]) -> list[dict]:
        for attempt in range(8):
            try:
                res = client.request("POST", "/api/memories/bulk", json={"items": chunk, "detect_duplicates": False})
                break
            except RateLimitError as exc:
                time.sleep(max(exc.retry_after or 1, 1))
            except MemoryAPIError as exc:
                if exc.status_code not in (502, 503, 504) or attempt == 7:
                    raise
                time.sleep(2 * (attempt + 1))
        errors = [r for r in res["results"] if r["status"] != "created"]
        if errors:
            raise SystemExit(f"ingestion failed for {len(errors)} items, e.g. {errors[:2]}")
        return res["results"]

    def ingest(self, client: MemoryClient, items: list[dict]) -> tuple[list[str], float]:
        """Bulk-imports items (100 per request, `workers` requests in parallel); returns memory ids in order."""
        chunks = [items[i:i + 100] for i in range(0, len(items), 100)]
        t0 = time.perf_counter()
        results = list(self.pool.map(lambda c: self._bulk(client, c), chunks))
        seconds = time.perf_counter() - t0
        ids = [r["memory_id"] for chunk in results for r in sorted(chunk, key=lambda r: r["index"])]
        self.created_memories.extend(ids)
        return ids, seconds

    @staticmethod
    def search(client: MemoryClient, **kw):
        while True:
            t0 = time.perf_counter()
            try:
                res = client.search(**kw)
                return res, (time.perf_counter() - t0) * 1000
            except RateLimitError as exc:  # waiting for the limiter is excluded from the timing
                time.sleep(max(exc.retry_after or 1, 1))

    def health(self) -> dict:
        return self.admin.request("GET", "/health")["components"]["embedding_model"]

    def cleanup(self, keep: bool) -> None:
        self.pool.shutdown(wait=True)
        if keep:
            log(f"--keep: leaving {len(self.created_memories)} memories in team {self.team_id}")
            return
        log(f"cleanup: deleting {len(self.created_memories)} benchmark memories and {len(self.created_agents)} agents")

        def delete(mid: str) -> None:
            try:
                self.admin.request("DELETE", f"/api/memories/{mid}", params={"permanent": "true"})
            except MemoryAPIError:
                pass

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            list(pool.map(delete, self.created_memories))
        for aid in self.created_agents:
            try:
                self.admin.request("DELETE", f"/api/agents/{aid}")
            except MemoryAPIError:
                pass
        self.admin.request("PATCH", f"/api/teams/{self.team_id}", json={"is_active": False})


def output_dir(base: str, name: str) -> Path:
    out = Path(base) / name
    try:
        out.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        out = Path("/tmp/evaluation-results") / name
        out.mkdir(parents=True, exist_ok=True)
    return out
