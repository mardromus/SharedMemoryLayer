#!/usr/bin/env python3
"""Controlled experiments on *where* authorisation happens in shared-memory retrieval.

The LoCoMo corpus is turned into a multi-tenant memory: every conversation belongs to its own team.
A question is asked by an agent of the team that owns its conversation (so its evidence is always
readable), while a fraction rho of every *other* team's memories is private to that team and the rest
is organisation-visible. Three experiments:

E1  The cost of filtering late (two corpora: LoCoMo, where teams hold topically disjoint
    conversations, and BEIR SciFact split at random over 10 teams, where teams hold topically
    overlapping knowledge as in a real organisation). Exact cosine search with bge-small-en-v1.5 (the embedding model of the
    system) under four strategies: pre-filter (authorise-before-rank, used by the system), post-filter
    (rank everything, drop unreadable results), post-filter with over-fetching (alpha * k candidates)
    and no filter. Reports Recall@10 of evidence, results returned and leakage.
E2  Corpus-statistics side channel. BM25 whose IDF / average length are computed over the whole index
    (as a shared lexical index would) versus document-local scores. Measures how often the ranking of
    the *readable* memories changes when only *unreadable* memories change.
E3  Filtered approximate search in Qdrant (HNSW): agreement with exact pre-filtered search and the
    latency of filtered versus unfiltered queries.

Usage (inside the backend container):
    docker compose exec backend python /opt/evaluation/ablation_filtering.py
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from bench_common import download, latency_summary, log, output_dir, tokens
from locomo_benchmark import CATEGORIES, DATA_URL, DIA, turns_of
import beir_benchmark

HERE = Path(__file__).resolve().parent
RHOS = (0.0, 0.25, 0.5, 0.75, 1.0)
ALPHAS = (1, 2, 5, 10)
K = 10


# ------------------------------------------------------------------ data
def build(data_path: Path):
    data = json.loads(download(DATA_URL, data_path).read_text(encoding="utf-8"))
    docs, owner, questions = [], [], []
    for team, sample in enumerate(data):
        turns = turns_of(sample)
        index = {}
        for t in turns:
            index[t["dia_id"]] = len(docs)
            docs.append(f"[{t['date']}] {t['speaker']}: {t['text']}")
            owner.append(team)
        for q in sample["qa"]:
            if q.get("category") not in (1, 2, 3, 4):
                continue
            ev = set()
            for e in q.get("evidence", []) or []:
                ev.update(DIA.findall(str(e)))
            ev = {index[d] for d in ev if d in index}
            if ev:
                questions.append({"team": team, "text": q["question"], "evidence": ev, "category": CATEGORIES[q["category"]]})
    return docs, np.array(owner), questions


def build_scifact(data_dir: Path, teams: int = 10, seed: int = 0):
    corpus, queries, qrels = beir_benchmark.load("scifact", data_dir)
    ids = list(corpus)
    pos = {d: i for i, d in enumerate(ids)}
    docs = [f"{corpus[d].get('title', '')}\n{corpus[d].get('text', '')}" for d in ids]
    rng = np.random.default_rng(seed)
    owner = rng.integers(0, teams, size=len(docs))
    questions = [{"team": int(rng.integers(0, teams)), "text": queries[q], "evidence": {pos[d] for d in rel if d in pos}}
                 for q, rel in qrels.items()]
    return docs, owner, [q for q in questions if q["evidence"]]


def embed(texts: list[str], model_name: str, cache: str) -> np.ndarray:
    import hashlib

    from fastembed import TextEmbedding

    key = hashlib.sha1((model_name + "\x00".join(texts)).encode()).hexdigest()[:16]
    cached = Path("/tmp") / f"ablation-emb-{key}.npy"
    if cached.exists():
        return np.load(cached)
    model = TextEmbedding(model_name=model_name, cache_dir=cache)
    vecs = np.array(list(model.embed(texts, batch_size=64)), dtype=np.float32)
    vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
    np.save(cached, vecs)
    return vecs


def visibility(owner: np.ndarray, rho: float, seed: int) -> np.ndarray:
    """org_visible[i] is True when memory i is organisation-visible; otherwise it is private to its team."""
    rng = np.random.default_rng(seed)
    return rng.random(len(owner)) >= rho


# ------------------------------------------------------------------ E1
def e1(doc_vecs, q_vecs, owner, questions, seeds, label="locomo"):
    sims = q_vecs @ doc_vecs.T  # exact cosine, (questions x docs)
    order = np.argsort(-sims, axis=1)
    results = {}
    for rho in RHOS:
        acc = defaultdict(list)
        for seed in seeds:
            org = visibility(owner, rho, seed)
            for qi, q in enumerate(questions):
                readable = org | (owner == q["team"])
                ranked_all = order[qi]
                ev = {d for d in q["evidence"] if readable[d]}  # the asker can only be expected to find what it may read
                if not ev:
                    acc["skipped_no_readable_evidence"].append(1.0)
                    continue
                acc["skipped_no_readable_evidence"].append(0.0)
                # pre-filter: exact top-k over the readable subset
                pre = [d for d in ranked_all if readable[d]][:K]
                acc["pre_recall"].append(len(ev & set(pre)) / len(ev))
                acc["pre_returned"].append(len(pre))
                # post-filter with over-fetch alpha
                for a in ALPHAS:
                    cand = ranked_all[: a * K]
                    kept = [d for d in cand if readable[d]][:K]
                    acc[f"post{a}_recall"].append(len(ev & set(kept)) / len(ev))
                    acc[f"post{a}_returned"].append(len(kept))
                    acc[f"post{a}_empty"].append(float(len(kept) == 0))
                    acc[f"post{a}_short"].append(float(len(kept) < K))
                # no filter
                top = ranked_all[:K]
                leaked = sum(1 for d in top if not readable[d])
                acc["none_recall"].append(len(ev & set(top)) / len(ev))
                acc["none_leak_rate"].append(leaked / K)
                acc["none_any_leak"].append(float(leaked > 0))
        results[str(rho)] = {k: round(statistics.fmean(v), 4) for k, v in acc.items()}
        log(f"E1[{label}] rho={rho}: pre {results[str(rho)]['pre_recall']:.3f} | post1 {results[str(rho)]['post1_recall']:.3f} "
            f"| post10 {results[str(rho)]['post10_recall']:.3f} | none leak {results[str(rho)]['none_leak_rate']:.3f}")
    return results


# ------------------------------------------------------------------ E2
class GlobalBM25:
    def __init__(self, toks: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.tf = [Counter(t) for t in toks]
        self.len = np.array([len(t) for t in toks], dtype=float)
        self.k1, self.b = k1, b

    def score(self, q_terms: list[str], docs: list[int], stat_docs: np.ndarray) -> dict[int, float]:
        """BM25 of `docs` with N, df and average length computed over `stat_docs` (the index statistics)."""
        n = len(stat_docs)
        avg = float(self.len[stat_docs].mean())
        out = {}
        df = {t: sum(1 for d in stat_docs if t in self.tf[d]) for t in set(q_terms)}
        idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in df}
        for d in docs:
            tf, dl, s = self.tf[d], self.len[d], 0.0
            for t in set(q_terms):
                if t in tf:
                    s += idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * dl / avg))
            out[d] = s
        return out


def e2(docs, owner, questions, seeds, sample_q: int):
    toks = [tokens(d) for d in docs]
    bm = GlobalBM25(toks)
    rng = random.Random(7)
    qs = rng.sample(range(len(questions)), min(sample_q, len(questions)))
    changed_order, changed_set, counted = 0, 0, 0
    score_changed = 0
    all_docs = np.arange(len(docs))
    for seed in seeds:
        org = visibility(owner, 0.5, seed)
        for qi in qs:
            q = questions[qi]
            readable = org | (owner == q["team"])
            r_docs = [int(d) for d in all_docs[readable]]
            hidden = all_docs[~readable]
            if len(hidden) == 0:
                continue
            # perturb only unreadable memories: drop every hidden memory of one other team
            teams = sorted({int(owner[d]) for d in hidden})
            drop = rng.choice(teams)
            stats_a = all_docs
            stats_b = np.array([d for d in all_docs if readable[d] or owner[d] != drop])
            terms = tokens(q["text"])
            sa, sb = bm.score(terms, r_docs, stats_a), bm.score(terms, r_docs, stats_b)
            top_a = [d for d, s in sorted(sa.items(), key=lambda x: (-x[1], x[0]))[:K] if s > 0]
            top_b = [d for d, s in sorted(sb.items(), key=lambda x: (-x[1], x[0]))[:K] if s > 0]
            changed_order += top_a != top_b
            changed_set += set(top_a) != set(top_b)
            score_changed += any(abs(sa[d] - sb[d]) > 1e-9 for d in top_a)
            counted += 1
    return {"questions": counted, "rho": 0.5, "perturbation": "remove all hidden memories of one other team",
            "bm25_global_score_of_visible_results_changed": round(score_changed / counted, 4),
            "bm25_global_top10_order_changed": round(changed_order / counted, 4),
            "bm25_global_top10_set_changed": round(changed_set / counted, 4),
            "document_local_scores": "cosine and ts_rank_cd depend only on (query, memory): unchanged by construction"}


# ------------------------------------------------------------------ E3
def e3(doc_vecs, q_vecs, owner, questions, seed: int, n_queries: int, rho: float = 0.5):
    from qdrant_client import QdrantClient
    from qdrant_client import models as qm

    client = QdrantClient(url=os.environ["QDRANT_URL"], api_key=os.environ.get("QDRANT_API_KEY") or None, timeout=60)
    name = f"ablation_{uuid.uuid4().hex[:8]}"
    client.create_collection(name, vectors_config=qm.VectorParams(size=doc_vecs.shape[1], distance=qm.Distance.COSINE),
                             optimizers_config=qm.OptimizersConfigDiff(indexing_threshold=10, default_segment_number=2))
    try:
        org = visibility(owner, rho, seed)
        client.create_payload_index(name, "team", qm.PayloadSchemaType.INTEGER, wait=True)
        client.create_payload_index(name, "org", qm.PayloadSchemaType.BOOL, wait=True)
        for start in range(0, len(doc_vecs), 512):
            pts = [qm.PointStruct(id=i, vector=doc_vecs[i].tolist(), payload={"team": int(owner[i]), "org": bool(org[i])})
                   for i in range(start, min(start + 512, len(doc_vecs)))]
            client.upsert(name, points=pts, wait=True)
        for _ in range(900):  # wait until every vector is in an HNSW segment
            info = client.get_collection(name)
            if info.status == qm.CollectionStatus.GREEN and (info.indexed_vectors_count or 0) >= len(doc_vecs):
                break
            time.sleep(1)
        indexed = client.get_collection(name).indexed_vectors_count
        sims = q_vecs @ doc_vecs.T
        rng = random.Random(seed)
        qs = rng.sample(range(len(questions)), min(n_queries, len(questions)))
        lat_f, lat_u, overlap, rec_ann, rec_exact = [], [], [], [], []
        for qi in qs:
            q = questions[qi]
            flt = qm.Filter(should=[qm.FieldCondition(key="org", match=qm.MatchValue(value=True)),
                                    qm.FieldCondition(key="team", match=qm.MatchValue(value=q["team"]))])
            t0 = time.perf_counter()
            res = client.query_points(name, query=q_vecs[qi].tolist(), query_filter=flt, limit=K, with_payload=False).points
            lat_f.append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            client.query_points(name, query=q_vecs[qi].tolist(), limit=K, with_payload=False)
            lat_u.append((time.perf_counter() - t0) * 1000)
            ann = [int(p.id) for p in res]
            readable = org | (owner == q["team"])
            exact = [int(d) for d in np.argsort(-sims[qi]) if readable[d]][:K]
            assert all(readable[d] for d in ann), "filtered search returned an unreadable memory"
            overlap.append(len(set(ann) & set(exact)) / K)
            rec_ann.append(len(q["evidence"] & set(ann)) / len(q["evidence"]))
            rec_exact.append(len(q["evidence"] & set(exact)) / len(q["evidence"]))
        return {"points": len(doc_vecs), "indexed_vectors": indexed, "queries": len(qs), "rho": rho,
                "readable_fraction_mean": round(float(np.mean([(org | (owner == questions[qi]["team"])).mean() for qi in qs])), 4),
                "unreadable_results": 0, "overlap_at_10_with_exact": round(statistics.fmean(overlap), 4),
                "recall_at_10_hnsw_filtered": round(statistics.fmean(rec_ann), 4), "recall_at_10_exact_prefilter": round(statistics.fmean(rec_exact), 4),
                "latency_ms_filtered": latency_summary(lat_f), "latency_ms_unfiltered": latency_summary(lat_u)}
    finally:
        client.delete_collection(name)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=str(HERE / "datasets" / "locomo10.json"))
    ap.add_argument("--model", default=os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
    ap.add_argument("--model-cache", default=os.environ.get("EMBEDDING_CACHE_DIR", "/opt/models"))
    ap.add_argument("--seeds", default="1,2,3")
    ap.add_argument("--e2-questions", type=int, default=400)
    ap.add_argument("--e3-questions", type=int, default=500)
    ap.add_argument("--out", default=str(HERE / "results"))
    args = ap.parse_args()
    Path(args.dataset).parent.mkdir(parents=True, exist_ok=True)
    seeds = [int(s) for s in args.seeds.split(",")]
    t0 = time.time()
    docs, owner, questions = build(Path(args.dataset))
    log(f"{len(docs)} memories in {owner.max() + 1} teams, {len(questions)} questions")
    doc_vecs = embed(docs, args.model, args.model_cache)
    q_vecs = embed([q["text"] for q in questions], args.model, args.model_cache)
    res = {"setup": {"memories": len(docs), "teams": int(owner.max() + 1), "questions": len(questions), "k": K,
                     "rhos": RHOS, "alphas": ALPHAS, "seeds": seeds, "embedding_model": args.model, "similarity": "exact cosine"}}
    res["e1_filtering_strategies"] = e1(doc_vecs, q_vecs, owner, questions, seeds, "locomo")
    s_docs, s_owner, s_questions = build_scifact(Path(args.dataset).parent)
    log(f"SciFact multi-tenant: {len(s_docs)} memories in {s_owner.max() + 1} teams, {len(s_questions)} questions")
    s_doc_vecs = embed(s_docs, args.model, args.model_cache)
    s_q_vecs = embed([q["text"] for q in s_questions], args.model, args.model_cache)
    res["setup"]["scifact"] = {"memories": len(s_docs), "teams": int(s_owner.max() + 1), "questions": len(s_questions),
                               "team_assignment": "uniformly random per document and per question (seed 0)"}
    res["e1_filtering_strategies_scifact"] = e1(s_doc_vecs, s_q_vecs, s_owner, s_questions, seeds, "scifact")
    res["e2_statistics_side_channel"] = e2(docs, owner, questions, seeds, args.e2_questions)
    log(f"E2: {res['e2_statistics_side_channel']}")
    res["e3_filtered_hnsw"] = {str(r): e3(doc_vecs, q_vecs, owner, questions, seeds[0], args.e3_questions, r) for r in (0.5, 1.0)}
    log(f"E3: {res['e3_filtered_hnsw']}")
    res["environment"] = {"finished_at": datetime.now(timezone.utc).isoformat(), "minutes": round((time.time() - t0) / 60, 1)}
    out = output_dir(args.out, f"ablation-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    (out / "results.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    log(f"results written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
