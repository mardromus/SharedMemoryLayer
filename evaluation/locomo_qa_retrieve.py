#!/usr/bin/env python3
"""Answer-accuracy benchmark on LoCoMo, step 1 of 2: retrieval.

Stores all LoCoMo turns through the public API (one agent and project per conversation), draws a
stratified random sample of the answerable questions (categories 1-4), and saves for every sampled
question the top-k memories returned by each configuration:

  sml_hybrid   this system, POST /api/memories/search (hybrid, the default)
  bm25         BM25 over the same turns
  dense        plain vector store (all-MiniLM-L6-v2 + cosine) over the same turns
  none         no memory (lower bound)

Step 2 (locomo_qa_answer.py) generates answers with an LLM and grades them with an LLM judge,
following the protocol of the Mem0 paper (LLM answer + LLM-as-a-judge, categories 1-4).

Usage (inside the backend container, ideally against the isolated benchmark instance):
    docker compose exec -e HF_HUB_OFFLINE=0 backend python /opt/evaluation/locomo_qa_retrieve.py \
        --base-url http://127.0.0.1:8001 --sample 200 --keep
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from bench_common import DEFAULT_DENSE_BASELINE, BM25, Harness, download, load_dense_baseline, log, output_dir
from locomo_benchmark import CATEGORIES, DATA_URL, DIA, turns_of

HERE = Path(__file__).resolve().parent


def stratified(questions: list[dict], n: int, seed: int) -> list[dict]:
    by_cat = defaultdict(list)
    for q in questions:
        by_cat[q["category"]].append(q)
    rng = random.Random(seed)
    total = len(questions)
    chosen = []
    for cat, qs in sorted(by_cat.items()):
        take = max(1, round(n * len(qs) / total))
        chosen += rng.sample(qs, min(take, len(qs)))
    rng.shuffle(chosen)
    return chosen[:n] if len(chosen) > n else chosen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=os.environ.get("SML_BASE_URL", "http://localhost:8000"))
    ap.add_argument("--dataset", default=str(HERE / "datasets" / "locomo10.json"))
    ap.add_argument("--sample", type=int, default=200)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--dense-baseline", default=DEFAULT_DENSE_BASELINE)
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    if not os.environ.get("ADMIN_USERNAME") or not os.environ.get("ADMIN_PASSWORD"):
        print("ADMIN_USERNAME / ADMIN_PASSWORD must be set", file=sys.stderr)
        return 2

    Path(args.dataset).parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(download(DATA_URL, Path(args.dataset)).read_text(encoding="utf-8"))
    dense = load_dense_baseline(args.dense_baseline)
    h = Harness(args.base_url, "LoCoMo-QA", workers=4)
    convs, all_questions = [], []
    try:
        for si, sample in enumerate(data):
            sid = sample.get("sample_id", f"conv-{si}")
            turns = turns_of(sample)
            client = h.agent(f"locomo-qa-{h.run_id}-{si}")
            project = h.project(f"locomo-qa-{h.run_id}-{sid}")
            items = [{"title": f"{t['speaker']} - session {t['session']} ({t['date']})"[:200],
                      "content": f"[{t['date']}] {t['speaker']}: {t['text']}", "memory_type": "context", "visibility": "team",
                      "project_id": project, "source": "locomo", "source_ref": t["dia_id"], "tags": ["locomo"]} for t in turns]
            ids, secs = h.ingest(client, items)
            texts = {t["dia_id"]: f"[{t['date']}] {t['speaker']}: {t['text']}" for t in turns}
            convs.append({"sid": sid, "client": client, "project": project, "id_to_dia": {mid: turns[i]["dia_id"] for i, mid in enumerate(ids)},
                          "texts": texts, "bm25": BM25({d: f"{t['speaker']} {t['date']} {t['text']}" for d, t in zip(texts, turns)}),
                          "dense": dense.index(texts) if dense else None})
            log(f"{sid}: {len(ids)} turns stored in {secs:.0f}s")
            for q in sample["qa"]:
                if q.get("category") not in (1, 2, 3, 4) or "answer" not in q:
                    continue
                ev = set()
                for e in q.get("evidence", []) or []:
                    ev.update(DIA.findall(str(e)))
                all_questions.append({"conv": si, "question": q["question"], "answer": str(q["answer"]),
                                      "category": CATEGORIES[q["category"]], "evidence": sorted(ev & set(texts))})

        sample_qs = stratified(all_questions, args.sample, args.seed)
        log(f"sampled {len(sample_qs)} of {len(all_questions)} questions: {dict(Counter(q['category'] for q in sample_qs))}")
        records = []
        for i, q in enumerate(sample_qs):
            c = convs[q["conv"]]
            res, _ = h.search(c["client"], query=q["question"], top_k=args.k, project_ids=[c["project"]], similarity_threshold=0.0)
            hybrid = [c["id_to_dia"][r.memory_id] for r in res if r.memory_id in c["id_to_dia"]]
            ranked = {"sml_hybrid": hybrid, "bm25": c["bm25"].rank(q["question"], args.k)}
            if c["dense"]:
                ranked["dense"] = c["dense"].rank(q["question"], args.k)
            ev = set(q["evidence"])
            records.append({
                "id": i, "conversation": c["sid"], "category": q["category"], "question": q["question"], "answer": q["answer"],
                "evidence": q["evidence"],
                "contexts": {name: [c["texts"][d] for d in docs] for name, docs in ranked.items()} | {"none": []},
                "evidence_recall": {name: (len(ev & set(docs)) / len(ev) if ev else None) for name, docs in ranked.items()},
            })
            if (i + 1) % 50 == 0:
                log(f"retrieved {i + 1}/{len(sample_qs)}")
    finally:
        h.cleanup(args.keep)

    out = output_dir(args.out, f"locomo-qa-{h.run_id}")
    meta = {"run_id": h.run_id, "dataset": "LoCoMo (locomo10.json)", "questions_total": len(all_questions), "sample": len(records),
            "seed": args.seed, "k": args.k, "by_category": dict(Counter(r["category"] for r in records)),
            "embedding_model": h.health().get("model"), "dense_baseline": dense.model_name if dense else None,
            "finished_at": datetime.now(timezone.utc).isoformat()}
    (out / "contexts.json").write_text(json.dumps({"meta": meta, "records": records}, indent=1), encoding="utf-8")
    log(f"contexts written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
