#!/usr/bin/env python3
"""BEIR retrieval benchmark (Thakur et al., NeurIPS 2021) for the Shared Memory Layer.

BEIR is the standard zero-shot benchmark for the retrieval component of RAG and memory systems; its
nDCG@10 scores are published for BM25, neural retrievers and — through MTEB — for the embedding
models other memory layers use by default. This script stores every document of a BEIR dataset as a
memory through the bulk-import API and issues every test query through /api/memories/search.

Datasets (small enough to ingest completely on a laptop):
  scifact   5 183 scientific abstracts, 300 claims to verify
  nfcorpus  3 633 medical documents, 323 health questions (graded relevance)

Metric: nDCG@10 with graded relevance (linear gain, as trec_eval/pytrec_eval used by BEIR),
plus Recall@10/@50 and search latency. Baselines over the same documents: BM25 and a plain vector
store (all-MiniLM-L6-v2 + cosine).

Usage (inside the backend container):
    docker compose exec -e HF_HUB_OFFLINE=0 backend python /opt/evaluation/beir_benchmark.py --base-url http://127.0.0.1:8001 --keep
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import statistics
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from bench_common import DEFAULT_DENSE_BASELINE, BM25, Harness, download, latency_summary, load_dense_baseline, log, output_dir

HERE = Path(__file__).resolve().parent
URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/{name}.zip"
CONFIGS = ("hybrid", "semantic", "bm25", "dense")
NAMES = {"hybrid": "**This system — hybrid (default)**", "semantic": "This system — semantic only", "bm25": "BM25 baseline (this script)",
         "dense": "Plain vector store (all-MiniLM-L6-v2, cosine)"}
_TAG = re.compile(r"<\s*/?\s*[a-zA-Z!][^>]*>")

# Published nDCG@10 (BEIR paper Table 2; MTEB results repository for the embedding models)
PUBLISHED = {
    "scifact": [("BM25 (Elasticsearch)", "BEIR paper", 0.665), ("DPR", "BEIR paper", 0.318), ("ANCE", "BEIR paper", 0.507),
                ("TAS-B", "BEIR paper", 0.643), ("ColBERT", "BEIR paper", 0.671), ("BM25 + cross-encoder re-ranker", "BEIR paper", 0.688),
                ("all-MiniLM-L6-v2 (Chroma's default embedder)", "MTEB", 0.645), ("OpenAI text-embedding-ada-002", "MTEB", 0.727),
                ("OpenAI text-embedding-3-small (Mem0's default embedder)", "MTEB", 0.734), ("bge-small-en-v1.5 (this system's model)", "MTEB", 0.713)],
    "nfcorpus": [("BM25 (Elasticsearch)", "BEIR paper", 0.325), ("DPR", "BEIR paper", 0.189), ("ANCE", "BEIR paper", 0.237),
                 ("TAS-B", "BEIR paper", 0.319), ("ColBERT", "BEIR paper", 0.305), ("BM25 + cross-encoder re-ranker", "BEIR paper", 0.350),
                 ("all-MiniLM-L6-v2 (Chroma's default embedder)", "MTEB", 0.316), ("OpenAI text-embedding-ada-002", "MTEB", 0.370),
                 ("OpenAI text-embedding-3-small (Mem0's default embedder)", "MTEB", 0.383), ("bge-small-en-v1.5 (this system's model)", "MTEB", 0.343)],
}


def load(name: str, base: Path) -> tuple[dict[str, dict], dict[str, str], dict[str, dict[str, int]]]:
    path = download(URL.format(name=name), base / f"{name}.zip")
    with zipfile.ZipFile(path) as z:
        read = lambda p: io.TextIOWrapper(z.open(f"{name}/{p}"), encoding="utf-8")  # noqa: E731
        corpus = {d["_id"]: d for d in map(json.loads, read("corpus.jsonl"))}
        queries = {q["_id"]: q["text"] for q in map(json.loads, read("queries.jsonl"))}
        qrels: dict[str, dict[str, int]] = {}
        for row in csv.DictReader(read("qrels/test.tsv"), delimiter="\t"):
            if int(row["score"]) > 0:
                qrels.setdefault(row["query-id"], {})[row["corpus-id"]] = int(row["score"])
    return corpus, {q: queries[q] for q in qrels}, qrels


def ndcg_at(ranked: list[str], rel: dict[str, int], k: int = 10) -> float:
    dcg = sum(rel.get(d, 0) / math.log2(i + 2) for i, d in enumerate(ranked[:k]))
    ideal = sum(g / math.log2(i + 2) for i, g in enumerate(sorted(rel.values(), reverse=True)[:k]))
    return dcg / ideal if ideal else 0.0


def metrics(ranked: list[str], rel: dict[str, int]) -> dict[str, float]:
    return {"ndcg@10": ndcg_at(ranked, rel), "recall@10": len(set(ranked[:10]) & set(rel)) / len(rel),
            "recall@50": len(set(ranked[:50]) & set(rel)) / len(rel), "hit@1": float(bool(ranked) and ranked[0] in rel)}


def to_item(doc: dict, project: str) -> dict:
    text = _TAG.sub(" ", doc.get("text") or "").strip()
    title = " ".join(_TAG.sub(" ", doc.get("title") or "").split())
    if len(title) < 3:
        title = (text[:80] or "Untitled document").split("\n")[0]
    return {"title": title[:200], "content": (text or title)[:20000], "memory_type": "documentation", "visibility": "team",
            "project_id": project, "source": "beir", "source_ref": doc["_id"][:255], "tags": ["beir"]}


def run_dataset(h: Harness, name: str, args, dense) -> dict:
    corpus, queries, qrels = load(name, Path(args.data_dir))
    log(f"{name}: {len(corpus)} documents, {len(queries)} test queries")
    client = h.agent(f"beir-{name}-{h.run_id}")
    project = h.project(f"beir-{name}-{h.run_id}")
    doc_ids = list(corpus)
    items = [to_item(corpus[d], project) for d in doc_ids]
    ids, secs = h.ingest(client, items)
    id_to_doc = dict(zip(ids, doc_ids))
    log(f"{name}: ingested {len(ids)} memories in {secs:.0f}s ({len(ids) / secs:.0f}/s)")
    texts = {d: f"{it['title']}\n{it['content']}" for d, it in zip(doc_ids, items)}
    bm25 = BM25(texts)
    dense_index = dense.index(texts) if dense else None
    per_q, latency = [], {m: {"client": [], "server": []} for m in ("hybrid", "semantic")}
    for qid, q in queries.items():
        row = {"query_id": qid}
        for mode in ("hybrid", "semantic"):
            res, ms = h.search(client, query=q[:2000], top_k=50, project_ids=[project], similarity_threshold=0.0, mode=mode)
            latency[mode]["client"].append(ms)
            latency[mode]["server"].append(res.took_ms)
            row[mode] = metrics([id_to_doc[x.memory_id] for x in res if x.memory_id in id_to_doc], qrels[qid])
        row["bm25"] = metrics(bm25.rank(q, 50), qrels[qid])
        if dense_index:
            row["dense"] = metrics(dense_index.rank(q, 50), qrels[qid])
        per_q.append(row)
    configs = [c for c in CONFIGS if c in per_q[0]]
    return {
        "documents": len(corpus), "queries": len(queries), "ingestion": {"items_per_second": round(len(ids) / secs, 1), "seconds": round(secs, 1)},
        "retrieval": {c: {m: round(statistics.fmean(r[c][m] for r in per_q), 4) for m in per_q[0][c]} for c in configs},
        "search_latency_ms": {m: {s: latency_summary(v) for s, v in d.items()} for m, d in latency.items()},
        "published_ndcg@10": PUBLISHED[name],
    }


def markdown(r: dict) -> str:
    lines = [f"# BEIR retrieval benchmark — run {r['run_id']}", "",
             f"Every document stored as a memory through the bulk-import API; every test query issued through `/api/memories/search` "
             f"(top 50). Embeddings: `{r['environment']['server']['model']}`. Primary metric: nDCG@10.", ""]
    for name, d in r["datasets"].items():
        lines += [f"## {name} ({d['documents']} documents, {d['queries']} test queries)", "",
                  "| Configuration | nDCG@10 | Recall@10 | Recall@50 | Hit@1 |", "|---|---|---|---|---|"]
        for c, m in d["retrieval"].items():
            lines.append(f"| {NAMES[c]} | {m['ndcg@10']:.3f} | {m['recall@10']:.3f} | {m['recall@50']:.3f} | {m['hit@1']:.3f} |")
        lat = d["search_latency_ms"]
        lines += ["", f"Search latency (client p50 / p95): hybrid {lat['hybrid']['client']['p50']} / {lat['hybrid']['client']['p95']} ms, "
                  f"semantic {lat['semantic']['client']['p50']} / {lat['semantic']['client']['p95']} ms. Ingestion {d['ingestion']['items_per_second']} memories/s.", "",
                  "Published nDCG@10 on the same dataset (not re-run here):", "", "| Retriever | Source | nDCG@10 |", "|---|---|---|"]
        for model, src, v in d["published_ndcg@10"]:
            lines.append(f"| {model} | {src} | {v:.3f} |")
        lines.append("")
    lines += ["## How to read this", "",
              "* BEIR numbers are from Thakur et al. (2021), Table 2; embedding-model numbers are from the MTEB results repository "
              "(embeddings-benchmark/results), which evaluates the same BEIR test sets with the same metric.",
              "* MTEB evaluates bge-small-en-v1.5 with its query instruction; this system embeds queries without an instruction, which "
              "explains small differences between the published bge-small figure and this system's semantic mode.",
              "* The BM25 row of this script uses a simple tokenizer without stemming; the BEIR paper's BM25 is Elasticsearch's."]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=os.environ.get("SML_BASE_URL", "http://localhost:8000"))
    ap.add_argument("--datasets", default="scifact,nfcorpus")
    ap.add_argument("--data-dir", default=str(HERE / "datasets"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dense-baseline", default=DEFAULT_DENSE_BASELINE)
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    if not os.environ.get("ADMIN_USERNAME") or not os.environ.get("ADMIN_PASSWORD"):
        print("ADMIN_USERNAME / ADMIN_PASSWORD must be set", file=sys.stderr)
        return 2
    t0 = time.time()
    dense = load_dense_baseline(args.dense_baseline)
    h = Harness(args.base_url, "BEIR", workers=args.workers)
    try:
        datasets = {name: run_dataset(h, name, args, dense) for name in args.datasets.split(",")}
    finally:
        h.cleanup(args.keep)
    results = {"run_id": h.run_id, "datasets": datasets, "baselines": {"dense_model": dense.model_name if dense else None},
               "environment": {"base_url": args.base_url, "finished_at": datetime.now(timezone.utc).isoformat(), "server": h.health()}}
    out = output_dir(args.out, f"beir-{h.run_id}")
    (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    (out / "results.md").write_text(markdown(results), encoding="utf-8")
    log(f"results written to {out} ({(time.time() - t0) / 60:.1f} min)")
    print((out / "results.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
