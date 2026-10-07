# LongMemEval retrieval benchmark — run 20260928-045828

419 questions of LongMemEval_S (knowledge-update: 72, multi-session: 121, single-session-assistant: 5, single-session-preference: 30, single-session-user: 64, temporal-reasoning: 127); 102229 user turns stored as memories (≈ 244.0 per question, one project per question). Embeddings: `BAAI/bge-small-en-v1.5`.

## Turn level (official protocol; Recall = all evidence turns retrieved)

| Configuration | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | Recall@50 | Any-evidence@10 |
|---|---|---|---|---|---|---|
| **This system — hybrid (default)** | 0.637 | 0.663 | 0.807 | 0.708 | 0.974 | 0.938 |
| This system — semantic only | 0.637 | 0.656 | 0.802 | 0.700 | 0.974 | 0.933 |
| BM25 baseline | 0.628 | 0.664 | 0.742 | 0.693 | 0.840 | 0.890 |
| Plain vector store (all-MiniLM-L6-v2, cosine) | 0.511 | 0.570 | 0.711 | 0.623 | 0.936 | 0.902 |

## Session level (sessions of the ranked turns)

| Configuration | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 |
|---|---|---|---|---|
| **This system — hybrid (default)** | 0.776 | 0.697 | 0.907 | 0.725 |
| This system — semantic only | 0.773 | 0.690 | 0.909 | 0.719 |
| BM25 baseline | 0.704 | 0.677 | 0.783 | 0.699 |
| Plain vector store (all-MiniLM-L6-v2, cosine) | 0.690 | 0.611 | 0.840 | 0.647 |

## Turn Recall@10 by question type

| Configuration | knowledge-update | multi-session | single-session-assistant | single-session-preference | single-session-user | temporal-reasoning |
|---|---|---|---|---|---|---|
| **This system — hybrid (default)** | 0.958 | 0.711 | 1.000 | 0.800 | 0.953 | 0.732 |
| This system — semantic only | 0.958 | 0.711 | 1.000 | 0.800 | 0.938 | 0.724 |
| BM25 baseline | 0.931 | 0.562 | 0.800 | 0.500 | 0.969 | 0.748 |
| Plain vector store (all-MiniLM-L6-v2, cosine) | 0.903 | 0.545 | 1.000 | 0.767 | 0.953 | 0.614 |

## Latency and ingestion

| Mode | client p50 | client p95 | server p50 | server p95 |
|---|---|---|---|---|
| hybrid | 83.9 ms | 188.7 ms | 68.7 ms | 164.9 ms |
| semantic | 78.5 ms | 191.0 ms | 66.4 ms | 170.3 ms |

Ingestion: 23.2 memories/s (4 parallel bulk requests, duplicate detection off).

## Published results (reported by their authors — not re-run here)

Retrieval, LongMemEval paper Table 3 — measured on **LongMemEval_M** (500 sessions ≈ 1.5M tokens per question, ~10× the haystack used here):

| Retriever | Level | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 |
|---|---|---|---|---|---|
| Stella V5 1.5B, key = value (round) | turn | 0.582 | 0.481 | 0.692 | 0.512 |
| Stella V5 1.5B, key = value + LLM-extracted facts (round) | turn | 0.644 | 0.498 | 0.784 | 0.536 |
| Stella V5 1.5B, key = value (session) | session | 0.706 | 0.617 | 0.783 | 0.638 |
| Stella V5 1.5B, key = value + LLM-extracted facts (session) | session | 0.732 | 0.620 | 0.862 | 0.652 |

End-to-end answer accuracy on LongMemEval_S (LLM judge):

| System | Source | Accuracy | Context tokens |
|---|---|---|---|
| GPT-4o, oracle (evidence sessions only) | LongMemEval paper | 87.0% | — |
| Zep + GPT-4o | Zep paper, Table 2 | 71.2% | 1600 |
| Zep + GPT-4o-mini | Zep paper, Table 2 | 63.8% | 1600 |
| GPT-4o, full context (no memory layer) | Zep paper, Table 2 | 60.2% | 115000 |
| GPT-4o-mini, full context (no memory layer) | Zep paper, Table 2 | 55.4% | 115000 |

## How to read this

* Protocol and metric definitions are ported from the official LongMemEval retrieval code; Recall@k is *recall_all* (every evidence turn in the top k).
* Session-level numbers are derived from the turn ranking (k is extended until k distinct sessions are covered, within the top 50 turns).
* The published retrieval numbers use the much larger M haystack and a 1.5B-parameter retriever, so they are a reference point, not a like-for-like comparison.
* The QA accuracies measure answers written by an LLM and graded by an LLM; this run measures retrieval only (no answering LLM).
