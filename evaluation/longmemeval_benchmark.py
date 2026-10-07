#!/usr/bin/env python3
"""LongMemEval retrieval benchmark (Wu et al., ICLR 2025) for the Shared Memory Layer.

LongMemEval tests the long-term memory of chat assistants: each of its 500 questions comes with its
own chat history (LongMemEval_S: ~48 sessions, ~115k tokens) in which the evidence turns are
annotated. It is the benchmark Zep and other memory layers report on besides LoCoMo.

This script follows the official retrieval protocol of the benchmark repository
(src/retrieval/run_retrieval.py, granularity "turn"):
  * every *user* turn of the question's history is one retrievable item (stored here as one memory,
    one project per question, through the bulk-import API)
  * evidence = user turns marked has_answer in the evidence sessions; abstention questions and
    questions without user-side evidence are skipped, exactly as in the official evaluation
  * metrics: recall_all@k (all evidence turns retrieved — the "Recall@k" of the paper) and
    ndcg_any@k at turn level, plus session-level metrics derived from the turn ranking
    (evaluate_retrieval_turn2session)

Configurations: this system (hybrid, semantic), BM25 and a plain vector store
(all-MiniLM-L6-v2 + cosine) over the same user turns.

Usage (inside the backend container, ideally against the isolated benchmark instance):
    docker compose exec -d backend sh /opt/evaluation/bench_instance.sh
    docker compose exec -e HF_HUB_OFFLINE=0 backend python /opt/evaluation/longmemeval_benchmark.py \
        --base-url http://127.0.0.1:8001 --keep
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from bench_common import DEFAULT_DENSE_BASELINE, BM25, Harness, download, latency_summary, load_dense_baseline, log, output_dir

HERE = Path(__file__).resolve().parent
DATA_URL = "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json"
KS = (5, 10, 50)
CONFIGS = ("hybrid", "semantic", "bm25", "dense")
NAMES = {"hybrid": "**This system — hybrid (default)**", "semantic": "This system — semantic only", "bm25": "BM25 baseline",
         "dense": "Plain vector store (all-MiniLM-L6-v2, cosine)"}

# LongMemEval paper (Wu et al., 2025), Table 3 — LongMemEval_M (500 sessions ≈ 1.5M tokens per question),
# Stella V5 1.5B retriever: Recall@5 / NDCG@5 / Recall@10 / NDCG@10.
PUBLISHED_RETRIEVAL = [
    ("Stella V5 1.5B, key = value (round)", "turn", 0.582, 0.481, 0.692, 0.512),
    ("Stella V5 1.5B, key = value + LLM-extracted facts (round)", "turn", 0.644, 0.498, 0.784, 0.536),
    ("Stella V5 1.5B, key = value (session)", "session", 0.706, 0.617, 0.783, 0.638),
    ("Stella V5 1.5B, key = value + LLM-extracted facts (session)", "session", 0.732, 0.620, 0.862, 0.652),
]
# End-to-end QA accuracy on LongMemEval_S (LLM judge) reported by other systems.
PUBLISHED_QA = [
    ("GPT-4o, oracle (evidence sessions only)", "LongMemEval paper", 87.0, None),
    ("Zep + GPT-4o", "Zep paper, Table 2", 71.2, 1600),
    ("Zep + GPT-4o-mini", "Zep paper, Table 2", 63.8, 1600),
    ("GPT-4o, full context (no memory layer)", "Zep paper, Table 2", 60.2, 115000),
    ("GPT-4o-mini, full context (no memory layer)", "Zep paper, Table 2", 55.4, 115000),
]


def dcg(rels: list[int]) -> float:
    """DCG exactly as in the official eval_utils.dcg (ranks 1 and 2 both have weight 1)."""
    if not rels:
        return 0.0
    return rels[0] + sum(r / math.log2(i + 1) for i, r in enumerate(rels) if i > 0)


def _evaluate(ranked: list[str], correct: set[str], n_relevant_in_corpus: int, k: int) -> tuple[float, float, float]:
    """Port of evaluate_retrieval(): recall_any, recall_all and ndcg_any at k."""
    top = ranked[:k]
    recalled = set(top)
    recall_any = float(any(c in recalled for c in correct))
    recall_all = float(all(c in recalled for c in correct))
    ideal = dcg([1] * min(n_relevant_in_corpus, k))
    ndcg = dcg([1 if d in correct else 0 for d in top]) / ideal if ideal else 0.0
    return recall_any, recall_all, ndcg


def _session(doc_id: str) -> str:
    return doc_id.rsplit("_", 1)[0]


def metrics(ranked: list[str], correct: set[str]) -> dict[str, float]:
    """Turn-level metrics and evaluate_retrieval_turn2session() session-level metrics.

    Doc ids follow the official naming: evidence turns keep their "answer_…" session id, the other
    turns of an evidence session are renamed "noans_…", so a session only counts as retrieved when
    one of its evidence turns is retrieved."""
    out = {}
    sessions = [_session(d) for d in ranked]
    correct_sessions = {_session(c) for c in correct}
    for k in KS:
        ra, rl, nd = _evaluate(ranked, correct, len(correct), k)
        out.update({f"turn_recall_any@{k}": ra, f"turn_recall_all@{k}": rl, f"turn_ndcg@{k}": nd})
        if k <= 10:
            eff = k  # extend k until k distinct sessions are covered (bounded by the 50 retrieved turns)
            while eff < len(sessions) and len(set(sessions[:eff])) < k:
                eff += 1
            ra, rl, nd = _evaluate(sessions, correct_sessions, len(correct), eff)
            out.update({f"session_recall_any@{k}": ra, f"session_recall_all@{k}": rl, f"session_ndcg@{k}": nd})
    return out


def corpus_of(entry: dict) -> tuple[list[tuple[str, str, str]], set[str]]:
    """(doc_id, date, text) for every non-empty user turn + the ids of the evidence turns (official naming)."""
    docs, correct = [], set()
    for sid, date, session in zip(entry["haystack_session_ids"], entry["haystack_dates"], entry["haystack_sessions"]):
        for i, turn in enumerate(session):
            if turn["role"] != "user" or not turn["content"].strip():
                continue
            doc_id = f"{sid}_{i + 1}"
            if "answer" in sid:
                if turn.get("has_answer"):
                    correct.add(doc_id)
                else:
                    doc_id = doc_id.replace("answer", "noans")
            docs.append((doc_id, date, turn["content"]))
    return docs, correct


def select(data: list[dict], sample: int | None, seed: int) -> list[dict]:
    eligible = [e for e in data if not e["question_id"].endswith("_abs") and corpus_of(e)[1]]
    if not sample or sample >= len(eligible):
        return eligible
    by_type = defaultdict(list)
    for e in eligible:
        by_type[e["question_type"]].append(e)
    rng = random.Random(seed)
    chosen = []
    for qtype, entries in sorted(by_type.items()):  # proportional stratified sample
        n = max(1, round(sample * len(entries) / len(eligible)))
        chosen += rng.sample(entries, min(n, len(entries)))
    return chosen


def run(args) -> Path:
    raw = json.loads(download(DATA_URL, Path(args.dataset)).read_text(encoding="utf-8"))
    skipped_abs = sum(e["question_id"].endswith("_abs") for e in raw)
    entries = select(raw, args.sample, args.seed)
    log(f"LongMemEval_S: {len(raw)} questions, {skipped_abs} abstention skipped, {len(entries)} evaluated")
    dense = load_dense_baseline(args.dense_baseline)
    h = Harness(args.base_url, "LongMemEval", workers=args.workers)
    per_q, latency = [], {m: {"client": [], "server": []} for m in ("hybrid", "semantic")}
    n_items, ingest_s = 0, 0.0
    try:
        client = h.agent(f"longmemeval-{h.run_id}")
        for qi, e in enumerate(entries):
            docs, correct = corpus_of(e)
            project = h.project(f"lme-{h.run_id}-{e['question_id']}")
            items = [{"title": "User message", "content": text[:20000], "memory_type": "context", "visibility": "team",
                      "project_id": project, "source": "longmemeval", "source_ref": doc_id[:255], "tags": ["longmemeval"]}
                     for doc_id, _, text in docs]
            ids, secs = h.ingest(client, items)
            n_items += len(ids)
            ingest_s += secs
            id_to_doc = {mid: docs[i][0] for i, mid in enumerate(ids)}
            row = {"question_id": e["question_id"], "type": e["question_type"], "n_turns": len(docs), "n_evidence": len(correct)}
            for mode in ("hybrid", "semantic"):
                res, ms = h.search(client, query=e["question"], top_k=50, project_ids=[project], similarity_threshold=0.0, mode=mode)
                latency[mode]["client"].append(ms)
                latency[mode]["server"].append(res.took_ms)
                row[mode] = metrics([id_to_doc[x.memory_id] for x in res if x.memory_id in id_to_doc], correct)
            texts = {d: t for d, _, t in docs}
            row["bm25"] = metrics(BM25(texts).rank(e["question"], 50), correct)
            if dense:
                row["dense"] = metrics(dense.index(texts).rank(e["question"], 50), correct)
            per_q.append(row)
            if (qi + 1) % 25 == 0 or qi + 1 == len(entries):
                done = [r["hybrid"]["turn_recall_all@10"] for r in per_q]
                log(f"{qi + 1}/{len(entries)} questions · {n_items} turns ingested ({n_items / ingest_s:.0f}/s) · running hybrid Recall@10 {statistics.fmean(done):.3f}")
    finally:
        h.cleanup(args.keep)

    configs = [c for c in CONFIGS if c in per_q[0]]
    summary = {c: {m: round(statistics.fmean(r[c][m] for r in per_q), 4) for m in per_q[0][c]} for c in configs}
    types = sorted({r["type"] for r in per_q})
    by_type = {c: {t: round(statistics.fmean(r[c]["turn_recall_all@10"] for r in per_q if r["type"] == t), 4) for t in types} for c in configs}
    results = {
        "run_id": h.run_id,
        "dataset": {"name": "LongMemEval_S (longmemeval_s_cleaned.json)", "questions_total": len(raw), "abstention_skipped": skipped_abs,
                    "questions_evaluated": len(per_q), "questions_by_type": dict(Counter(r["type"] for r in per_q)),
                    "user_turns_ingested": n_items, "avg_user_turns_per_question": round(n_items / len(per_q), 1),
                    "sample": args.sample, "seed": args.seed},
        "retrieval": summary, "turn_recall_all@10_by_type": by_type,
        "search_latency_ms": {m: {s: latency_summary(v) for s, v in d.items()} for m, d in latency.items()},
        "ingestion": {"items_per_second": round(n_items / ingest_s, 1), "total_seconds": round(ingest_s, 1), "parallel_requests": args.workers},
        "baselines": {"dense_model": dense.model_name if dense else None},
        "published_reference": {"longmemeval_paper_table3_M": PUBLISHED_RETRIEVAL, "qa_accuracy_S": PUBLISHED_QA},
        "environment": {"base_url": args.base_url, "finished_at": datetime.now(timezone.utc).isoformat(), "server": h.health()},
    }
    out = output_dir(args.out, f"longmemeval-{h.run_id}")
    (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    with (out / "per_question.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["question_id", "type", "n_turns", "n_evidence", *[f"{c}_turn_recall_all@10" for c in configs], *[f"{c}_session_recall_all@10" for c in configs]])
        for r in per_q:
            w.writerow([r["question_id"], r["type"], r["n_turns"], r["n_evidence"], *[r[c]["turn_recall_all@10"] for c in configs], *[r[c]["session_recall_all@10"] for c in configs]])
    (out / "results.md").write_text(markdown(results, configs), encoding="utf-8")
    return out


def markdown(r: dict, configs: list[str]) -> str:
    s, ds, lat = r["retrieval"], r["dataset"], r["search_latency_ms"]
    lines = [f"# LongMemEval retrieval benchmark — run {r['run_id']}", "",
             f"{ds['questions_evaluated']} questions of LongMemEval_S ({', '.join(f'{k}: {v}' for k, v in sorted(ds['questions_by_type'].items()))}); "
             f"{ds['user_turns_ingested']} user turns stored as memories (≈ {ds['avg_user_turns_per_question']} per question, one project per question). "
             f"Embeddings: `{r['environment']['server']['model']}`.", "",
             "## Turn level (official protocol; Recall = all evidence turns retrieved)", "",
             "| Configuration | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | Recall@50 | Any-evidence@10 |", "|---|---|---|---|---|---|---|"]
    for c in configs:
        m = s[c]
        lines.append(f"| {NAMES[c]} | {m['turn_recall_all@5']:.3f} | {m['turn_ndcg@5']:.3f} | {m['turn_recall_all@10']:.3f} | {m['turn_ndcg@10']:.3f} | {m['turn_recall_all@50']:.3f} | {m['turn_recall_any@10']:.3f} |")
    lines += ["", "## Session level (sessions of the ranked turns)", "", "| Configuration | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 |", "|---|---|---|---|---|"]
    for c in configs:
        m = s[c]
        lines.append(f"| {NAMES[c]} | {m['session_recall_all@5']:.3f} | {m['session_ndcg@5']:.3f} | {m['session_recall_all@10']:.3f} | {m['session_ndcg@10']:.3f} |")
    types = sorted(r["turn_recall_all@10_by_type"]["hybrid"])
    lines += ["", "## Turn Recall@10 by question type", "", "| Configuration | " + " | ".join(types) + " |", "|---|" + "---|" * len(types)]
    for c in configs:
        lines.append(f"| {NAMES[c]} | " + " | ".join(f"{r['turn_recall_all@10_by_type'][c][t]:.3f}" for t in types) + " |")
    lines += ["", "## Latency and ingestion", "", "| Mode | client p50 | client p95 | server p50 | server p95 |", "|---|---|---|---|---|"]
    for m in ("hybrid", "semantic"):
        lines.append(f"| {m} | {lat[m]['client']['p50']} ms | {lat[m]['client']['p95']} ms | {lat[m]['server']['p50']} ms | {lat[m]['server']['p95']} ms |")
    lines += ["", f"Ingestion: {r['ingestion']['items_per_second']} memories/s ({r['ingestion']['parallel_requests']} parallel bulk requests, duplicate detection off).", "",
              "## Published results (reported by their authors — not re-run here)", "",
              "Retrieval, LongMemEval paper Table 3 — measured on **LongMemEval_M** (500 sessions ≈ 1.5M tokens per question, ~10× the haystack used here):", "",
              "| Retriever | Level | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 |", "|---|---|---|---|---|---|"]
    for name, level, r5, n5, r10, n10 in PUBLISHED_RETRIEVAL:
        lines.append(f"| {name} | {level} | {r5:.3f} | {n5:.3f} | {r10:.3f} | {n10:.3f} |")
    lines += ["", "End-to-end answer accuracy on LongMemEval_S (LLM judge):", "", "| System | Source | Accuracy | Context tokens |", "|---|---|---|---|"]
    for name, src, acc, tok in PUBLISHED_QA:
        lines.append(f"| {name} | {src} | {acc}% | {tok or '—'} |")
    lines += ["", "## How to read this", "",
              "* Protocol and metric definitions are ported from the official LongMemEval retrieval code; Recall@k is *recall_all* (every evidence turn in the top k).",
              "* Session-level numbers are derived from the turn ranking (k is extended until k distinct sessions are covered, within the top 50 turns).",
              "* The published retrieval numbers use the much larger M haystack and a 1.5B-parameter retriever, so they are a reference point, not a like-for-like comparison.",
              "* The QA accuracies measure answers written by an LLM and graded by an LLM; this run measures retrieval only (no answering LLM)."]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=os.environ.get("SML_BASE_URL", "http://localhost:8000"))
    ap.add_argument("--dataset", default=str(HERE / "datasets" / "longmemeval_s_cleaned.json"))
    ap.add_argument("--sample", type=int, default=None, help="stratified sample of N questions (default: all eligible)")
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--workers", type=int, default=4, help="parallel bulk-import requests")
    ap.add_argument("--dense-baseline", default=DEFAULT_DENSE_BASELINE, help="fastembed model for the plain-vector-store baseline ('' to skip)")
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--keep", action="store_true", help="do not delete the benchmark memories (use with the isolated instance)")
    args = ap.parse_args()
    if not os.environ.get("ADMIN_USERNAME") or not os.environ.get("ADMIN_PASSWORD"):
        print("ADMIN_USERNAME / ADMIN_PASSWORD must be set", file=sys.stderr)
        return 2
    t0 = time.time()
    out = run(args)
    log(f"results written to {out} ({(time.time() - t0) / 60:.1f} min)")
    print((out / "results.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
