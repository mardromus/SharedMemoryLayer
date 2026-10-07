# BEIR retrieval benchmark — run 20260928-043112

Every document stored as a memory through the bulk-import API; every test query issued through `/api/memories/search` (top 50). Embeddings: `BAAI/bge-small-en-v1.5`. Primary metric: nDCG@10.

## scifact (5183 documents, 300 test queries)

| Configuration | nDCG@10 | Recall@10 | Recall@50 | Hit@1 |
|---|---|---|---|---|
| **This system — hybrid (default)** | 0.718 | 0.838 | 0.930 | 0.607 |
| This system — semantic only | 0.717 | 0.838 | 0.930 | 0.607 |
| BM25 baseline (this script) | 0.666 | 0.782 | 0.875 | 0.550 |
| Plain vector store (all-MiniLM-L6-v2, cosine) | 0.645 | 0.783 | 0.892 | 0.503 |

Search latency (client p50 / p95): hybrid 108.7 / 197.2 ms, semantic 77.0 / 143.0 ms. Ingestion 13.8 memories/s.

Published nDCG@10 on the same dataset (not re-run here):

| Retriever | Source | nDCG@10 |
|---|---|---|
| BM25 (Elasticsearch) | BEIR paper | 0.665 |
| DPR | BEIR paper | 0.318 |
| ANCE | BEIR paper | 0.507 |
| TAS-B | BEIR paper | 0.643 |
| ColBERT | BEIR paper | 0.671 |
| BM25 + cross-encoder re-ranker | BEIR paper | 0.688 |
| all-MiniLM-L6-v2 (Chroma's default embedder) | MTEB | 0.645 |
| OpenAI text-embedding-ada-002 | MTEB | 0.727 |
| OpenAI text-embedding-3-small (Mem0's default embedder) | MTEB | 0.734 |
| bge-small-en-v1.5 (this system's model) | MTEB | 0.713 |

## nfcorpus (3633 documents, 323 test queries)

| Configuration | nDCG@10 | Recall@10 | Recall@50 | Hit@1 |
|---|---|---|---|---|
| **This system — hybrid (default)** | 0.359 | 0.174 | 0.263 | 0.480 |
| This system — semantic only | 0.342 | 0.165 | 0.255 | 0.440 |
| BM25 baseline (this script) | 0.312 | 0.152 | 0.212 | 0.443 |
| Plain vector store (all-MiniLM-L6-v2, cosine) | 0.316 | 0.155 | 0.251 | 0.418 |

Search latency (client p50 / p95): hybrid 90.1 / 179.8 ms, semantic 75.5 / 181.3 ms. Ingestion 14.2 memories/s.

Published nDCG@10 on the same dataset (not re-run here):

| Retriever | Source | nDCG@10 |
|---|---|---|
| BM25 (Elasticsearch) | BEIR paper | 0.325 |
| DPR | BEIR paper | 0.189 |
| ANCE | BEIR paper | 0.237 |
| TAS-B | BEIR paper | 0.319 |
| ColBERT | BEIR paper | 0.305 |
| BM25 + cross-encoder re-ranker | BEIR paper | 0.350 |
| all-MiniLM-L6-v2 (Chroma's default embedder) | MTEB | 0.316 |
| OpenAI text-embedding-ada-002 | MTEB | 0.370 |
| OpenAI text-embedding-3-small (Mem0's default embedder) | MTEB | 0.383 |
| bge-small-en-v1.5 (this system's model) | MTEB | 0.343 |

## How to read this

* BEIR numbers are from Thakur et al. (2021), Table 2; embedding-model numbers are from the MTEB results repository (embeddings-benchmark/results), which evaluates the same BEIR test sets with the same metric.
* MTEB evaluates bge-small-en-v1.5 with its query instruction; this system embeds queries without an instruction, which explains small differences between the published bge-small figure and this system's semantic mode.
* The BM25 row of this script uses a simple tokenizer without stemming; the BEIR paper's BM25 is Elasticsearch's.
