# LoCoMo retrieval benchmark — run 20260927-200109

10 conversations, 5882 dialog turns stored as memories, 1535 answerable questions (temporal: 320, open-domain: 92, multi-hop: 282, single-hop: 841). Embeddings: `BAAI/bge-small-en-v1.5`.

## Evidence retrieval (higher is better)

| Configuration | Recall@5 | Recall@10 | Recall@25 | Recall@50 | Hit@1 | Hit@10 | MRR | nDCG@10 |
|---|---|---|---|---|---|---|---|---|
| **This system — hybrid** | 0.582 | 0.673 | 0.789 | 0.852 | 0.392 | 0.741 | 0.511 | 0.523 |
| This system — semantic only | 0.561 | 0.657 | 0.783 | 0.848 | 0.352 | 0.725 | 0.478 | 0.494 |
| This system — keyword only (AND) | 0.119 | 0.121 | 0.122 | 0.123 | 0.118 | 0.138 | 0.126 | 0.118 |
| BM25 baseline | 0.523 | 0.593 | 0.669 | 0.719 | 0.345 | 0.652 | 0.450 | 0.463 |
| Recency baseline (last-k turns) | 0.002 | 0.010 | 0.034 | 0.078 | 0.001 | 0.011 | 0.005 | 0.004 |
| *Published: LoCoMo paper, DRAGON retriever over dialogs†* | 0.588 | 0.675 | 0.799 | 0.848 | — | — | — | — |

## Recall@10 by question category

| Configuration | multi-hop | open-domain | single-hop | temporal |
|---|---|---|---|---|
| **This system — hybrid** | 0.488 | 0.473 | 0.733 | 0.736 |
| This system — semantic only | 0.485 | 0.473 | 0.711 | 0.720 |
| This system — keyword only (AND) | 0.063 | 0.025 | 0.117 | 0.211 |
| BM25 baseline | 0.277 | 0.267 | 0.699 | 0.688 |
| Recency baseline (last-k turns) | 0.004 | 0.014 | 0.012 | 0.009 |

## Latency and cost

| Mode | search p50 (client) | search p95 (client) | search p50 (server) | search p95 (server) |
|---|---|---|---|---|
| hybrid | 75.0 ms | 197.4 ms | 62.1 ms | 170.5 ms |
| semantic | 71.2 ms | 198.6 ms | 57.9 ms | 171.6 ms |
| keyword | 12.7 ms | 83.0 ms | 2.1 ms | 56.7 ms |

Ingestion: 17.7 turns/s (bulk import, duplicate detection off). Top-10 hybrid context ≈ 441 tokens vs ≈ 21475 tokens for a whole conversation (≈ 4 characters per token).

## Published results of other memory layers on LoCoMo (reported by their authors — not re-run here)

| System | Overall J (LLM judge) | Search p50 | Search p95 | Memory tokens / conversation |
|---|---|---|---|---|
| Full-context (no memory layer) | 72.9% | — | — | 26031 |
| Mem0g (graph) | 68.44% | 0.476s | 0.657s | 3616 |
| Mem0 | 66.88% | 0.148s | 0.2s | 1764 |
| Zep | 65.99% | 0.513s | 0.778s | 3911 |
| LangMem | 58.1% | 17.99s | 59.82s | 127 |
| OpenAI memory | 52.9% | — | — | 4437 |
| A-Mem | 48.38% | 0.668s | 1.485s | 2520 |
| Best RAG (k=2, 256-token chunks) | 60.97% | 0.255s | 0.699s | — |

This system (measured above): search p50 0.075s, p95 0.197s (hybrid, k=50, same machine as the database); retrieved context ≈ 441 tokens at k=10. J score not measured (needs an answering + judging LLM).

## How to read this

* † The LoCoMo paper evaluated DRAGON on its full 50-conversation set; this run uses the 10 publicly released conversations (`locomo10.json`) — the same subset the Mem0 paper used. Recall@k = fraction of annotated evidence turns retrieved.
* The Mem0-paper rows measure *answer accuracy* with GPT-4o-mini as answerer and judge, and latency against hosted services over the network. They are **not directly comparable** to retrieval recall; they are listed so the evaluated dimensions line up (accuracy, search latency, context size). Zep separately reports 94.8% vs MemGPT 93.4% on the DMR benchmark (Rasmussen et al., 2025).
* Category 5 (adversarial) questions are excluded, as in the Mem0 evaluation, because their answers are not in the conversation.
* Keyword-only mode requires every query term to match (full-text AND), so it is precise but misses paraphrases; hybrid combines it with embeddings.
