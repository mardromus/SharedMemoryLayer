# Evaluation

All numbers in this document were **measured** by `evaluation/run_evaluation.py` against the running
Docker Compose deployment through the public REST API (Python SDK). Nothing is simulated inside the
server and no value is hand-entered. The raw outputs of the reference runs are kept in
[`docs/evaluation-runs/`](evaluation-runs/) (`results.md`, `results.json`, `per_query.csv`).

| Run | LLM provider | Extras | Folder |
|---|---|---|---|
| `20260927-180208` | none (extractive synthesis) | 3 latency repeats, scale test to 1 000 memories | `evaluation-runs/20260927-180208-extractive-scale1000/` |
| `20260927-175138` | Ollama `qwen2.5:0.5b` (local, CPU, 4 threads) | 1 latency repeat | `evaluation-runs/20260927-175138-ollama-qwen2.5-0.5b/` |
| `20260927-202610` | none (extractive synthesis) | re-run after **hybrid** search became the default: every retrieval, agent-task and security figure identical to the runs above | `evaluation-runs/20260927-202610-hybrid-default/` |
| `locomo-20260927-200109` | — (retrieval benchmark) | public **LoCoMo** benchmark, all 10 released conversations, main instance | `evaluation-runs/locomo-20260927-200109-full10/` |
| `locomo-20260928-044654` | — | LoCoMo again on the isolated benchmark instance, with the plain-vector-store baseline (§3.2) | `evaluation-runs/locomo-20260928-044654-isolated/` |
| `longmemeval-20260928-045828` | — | public **LongMemEval_S** benchmark, all 419 evaluable questions, 102 229 memories (§3.3) | `evaluation-runs/longmemeval-20260928-045828/` |
| `beir-20260928-043112` | — | public **BEIR** SciFact + NFCorpus (§3.4) | `evaluation-runs/beir-20260928-043112/` |
| `ablation-20260929-170548` | — | placement of authorisation: rank-then-filter vs authorise-before-rank, BM25 side channel, filtered HNSW (§4) | `evaluation-runs/ablation-20260929-170548/` |
| `locomo-qa-20261006-072432` | Ollama `gemma4:latest` (8B, local) as answerer and judge | LoCoMo **answer accuracy** with an LLM judge, 135 sampled questions (§5) | `evaluation-runs/locomo-qa-20261006-072432/` |

**Environment**: Windows 11 ARM64 laptop, Docker Desktop (WSL2, 12 vCPU, 15.5 GB), all services in one
Compose project; client = the benchmark running inside the backend container
(Python 3.12, `http://localhost:8000`). Embeddings: `BAAI/bge-small-en-v1.5` (384-d, ONNX, CPU).

## 1. Method

### 1.1 Corpus and queries (`evaluation/dataset.json`)

* **35 labelled memories** written through the API by four freshly created team agents
  (Development 10, QA 8, Security 9, Support 8); 28 organisation-visible, 6 team-only (4 of them
  confidential) and 1 shared with a selected team.
* **40 queries**, 10 per team, each with ground-truth relevant memory keys. Queries are deliberate
  **paraphrases** (e.g. *"text message verification codes never arrive for some users"* for the memory
  *"SMS one-time codes not delivered"*). **25 queries can only be answered with another team's
  knowledge.**
* Each run creates its own teams, agents and project and deletes them afterwards, so runs are isolated
  from each other and from the demo data (all searches are scoped to the run's project).

### 1.2 Configurations

| Configuration | Meaning |
|---|---|
| **Isolated** (baseline) | each agent can only retrieve memories it created itself (`only_own=true`) — today's per-agent memory silo |
| **Shared** (proposed) | permission-aware semantic search over the shared layer |
| **Lexical** (reference) | TF-IDF cosine ranking over *exactly the memories the agent may read* — separates the effect of semantic embeddings from the effect of sharing |

### 1.3 Metrics

| Metric | Definition |
|---|---|
| Hit@1, Hit@5 | a relevant memory is ranked 1st / in the top 5 |
| Precision@5, Recall@5 | relevant results in the top 5 ÷ 5; ÷ number of relevant memories |
| MRR, nDCG@5 | rank-sensitive quality of the top-5 list |
| Search precision | of all results at or above the similarity threshold (0.55), the fraction that is relevant |
| Answered correctly | the top result is above the threshold **and** relevant |
| Knowledge-reuse rate | fraction of queries answered (above threshold) with a relevant memory owned by *another* team |
| Retrieval latency | client round-trip and server-side processing time per search (mean, p50, p95, p99) |
| Duplicate-task rate | fraction of agent tasks whose knowledge already existed in the organisation but was **not** found, so the agent had to re-derive and store it again |
| Duplicates flagged | how many of those re-stored memories the server's duplicate detector flagged |
| Task completion time | agent-runtime wall time (search + synthesis/LLM) **plus** the measured cost of storing the re-derived knowledge when memory did not help |
| Security | adversarial attempts by every agent outside the authorised teams against every protected memory (direct read, update, version history, sharing to itself, semantic search by title and by content) plus privilege-escalation attempts (create team/agent, read audit log, list users, re-index); blocked vs. leaked, and whether each denial was written to the audit log |

## 2. Results

### 2.1 Retrieval quality (identical in both runs — retrieval is deterministic)

| Metric | Isolated (baseline) | **Shared (proposed)** | Lexical TF-IDF on shared set |
|---|---|---|---|
| Hit@1 | 0.350 | **0.975** | 0.850 |
| Hit@5 | 0.375 | **1.000** | 1.000 |
| Precision@5 | 0.075 | **0.235** | 0.245 |
| Recall@5 | 0.338 | **0.963** | 0.988 |
| MRR | 0.358 | **0.983** | 0.921 |
| nDCG@5 | 0.338 | **0.957** | 0.927 |
| Hit@5 on the 25 cross-team queries | 0.000 | **1.000** | 1.000 |
| Search precision (≥ 0.55) | 0.103 | **0.243** | — |
| Answered correctly | 0.350 | **0.975** | — |
| Knowledge-reuse rate | 0.000 | **0.675** | — |

*Reading:* sharing is what makes 25 of 40 questions answerable at all (Hit@5 0.375 → 1.0). Semantic
embeddings mainly improve the **ranking** (Hit@1 0.85 → 0.975, MRR 0.92 → 0.98) compared with keyword
matching over the same memories; with only 35 documents TF-IDF already recalls almost everything in the
top 5. Precision@5 is bounded by the small number of relevant memories per query (1–3), so ≈0.2–0.25 is
near its ceiling here. *Search precision* is low (0.24) because the 0.55 threshold is permissive:
results above it include related-but-not-labelled memories; raising the threshold trades recall for
precision.

### 2.2 Latency (run `20260927-180208`, 120 searches per configuration)

| Configuration | Side | mean | p50 | p95 | p99 |
|---|---|---|---|---|---|
| Isolated | client | 107.3 | 100.2 | 168.5 | 223.6 |
| Isolated | server | 86.8 | 80.8 | 142.4 | 185.4 |
| **Shared** | client | 112.9 | 103.3 | 207.5 | 270.2 |
| **Shared** | server | 91.9 | 83.4 | 162.0 | 211.9 |

Server time covers query embedding, the filtered Qdrant search, the PostgreSQL re-check, ranking,
access-counter updates and the audit write. Shared search costs ≈3 ms more than isolated search at the
median (more candidates pass the filter and are re-checked).
Memory creation (validation, embedding, duplicate + conflict detection, PostgreSQL + Qdrant writes,
version, audit): mean 205 ms, p50 189 ms, p95 336 ms.

**Latency vs. corpus size** (40 queries at each checkpoint, same project filter):

| Memories | client p50 | client p95 | server p50 | server p95 |
|---|---|---|---|---|
| 100 | 98.1 | 131.5 | 80.3 | 111.7 |
| 250 | 111.8 | 254.4 | 89.6 | 152.1 |
| 500 | 108.4 | 150.4 | 89.7 | 129.9 |
| 1 000 | 110.7 | 193.9 | 93.1 | 175.6 |

Median search latency stays ≈ 80–95 ms server-side as the corpus grows 10×, which suggests the cost is
dominated by per-request work (query embedding, database round trips, auditing) rather than by the
vector search itself. p95 is noisier (single laptop, other
containers running). Bulk writes during the scale test averaged 264 ms.

### 2.3 Agent tasks: reuse, duplicate work, completion time

| Metric | Isolated | **Shared** |
|---|---|---|
| Tasks resolved from existing memory | 0.375 | **1.000** |
| Duplicate-task rate | 0.625 | **0.000** |
| Re-stored memories flagged as duplicates by the server | 25 / 25 | — |
| Tasks whose context included another team's memory | 0 | 39 / 40 |

These rates are the same with and without an LLM (they depend on retrieval only). Every one of the
25 re-derived memories was flagged by the duplicate detector as *"Potential duplicate memories
found."* — evidence that the duplicate check catches exactly the redundant work the isolated agents
performed.

**Task completion time** depends strongly on the reasoning component:

| Run | Configuration | Agent runtime mean / p95 (ms) | Completion incl. re-derivation mean / p95 (ms) |
|---|---|---|---|
| Extractive (no LLM) | Isolated | 118 / 193 | 265 / 451 |
| Extractive (no LLM) | **Shared** | 122 / 222 | **122 / 222** |
| Ollama qwen2.5:0.5b | Isolated | 3 430 / 6 354 | **3 574 / 6 557** |
| Ollama qwen2.5:0.5b | Shared | 4 283 / 13 970 | 4 283 / 13 970 |

* Without an LLM, shared memory halves completion time because the isolated agent must additionally
  re-derive and store 62.5 % of its answers.
* With a real (small, CPU-only) LLM the picture reverses: inference dominates, and the shared agent
  sends **more retrieved context** to the model, so its mean task time is ≈ 20 % higher (p95 roughly
  2× because long contexts slow the CPU model). The re-derivation cost in the isolated configuration
  is modelled only as *storing* the answer (≈ 230 ms); the isolated agent's answer to 25 tasks is
  ungrounded — the model answered without the organisation's knowledge. A fair statement is therefore:
  **the shared layer made every answer grounded in existing organisational knowledge and eliminated
  duplicate work, at the cost of longer LLM prompts.** Capping the context (smaller `top_k`, a
  similarity threshold, or summarising memories) is the obvious optimisation.

### 2.4 Security (both runs identical)

| Attempt | Attempts | Blocked |
|---|---|---|
| Direct read of a protected memory | 20 | 20 |
| Update | 20 | 20 |
| Read version history | 20 | 20 |
| Share the memory to one's own team | 20 | 20 |
| Semantic search with the memory's title | 20 | 20 |
| Semantic search with the memory's content | 20 | 20 |
| Escalation: create team / create agent / read audit log / list users / re-index (4 agents each) | 20 | 20 |
| **Total** | **140** | **140** |

**Leaked: 0** — including the 40 semantic searches that used the protected memory's own text as the
query, the strongest possible similarity attack. **All 100 denials were found in the audit log**
(the 40 search attempts are not denials — the memories are simply never scored — and are audited as
ordinary searches).

## 3. Public benchmarks, and comparison with other memory layers

The in-house corpus above answers the project's own question (does *sharing* help?), but it is small and written by the author. To compare the system with other memory layers and retrievers on neutral ground it was also run, end to end through its API, on three public benchmarks:

| Benchmark | What it tests | Scale of this run | Published results used for comparison |
|---|---|---|---|
| **LoCoMo** (Maharana et al., ACL 2024) | recalling facts from very long multi-session conversations | 10 conversations, 5 882 turns, 1 535 questions | Mem0, Mem0ᵍ, Zep, LangMem, A-Mem, OpenAI memory (Mem0 paper); DRAGON retriever (LoCoMo paper) |
| **LongMemEval** (Wu et al., ICLR 2025) | long-term memory of a chat assistant — each question has its own history of ≈ 48 sessions | 419 questions, 102 229 user messages stored | Zep and full-context GPT-4o (Zep paper); Stella V5 retriever (LongMemEval paper) |
| **BEIR** (Thakur et al., NeurIPS 2021) | zero-shot document retrieval, the step every memory layer depends on | SciFact + NFCorpus: 8 816 documents, 623 queries | BM25, DPR, ANCE, TAS-B, ColBERT, BM25 + cross-encoder (BEIR paper); embedding models incl. Mem0's and Chroma's defaults (MTEB) |

### 3.1 How the benchmarks are run

* **Isolated benchmark instance.** `evaluation/bench_instance.sh` starts a second API process from the same image on port 8001 with its own PostgreSQL database and Qdrant collection, so 100 000+ benchmark memories never touch the demo organisation or its append-only audit log. Rate limiting is disabled on that instance only (the benchmark measures retrieval, not throttling); `bench_instance.sh --drop` removes everything afterwards.
* **Through the public API.** Every turn or document is stored as one memory with `POST /api/memories/bulk` (4 requests in parallel, duplicate detection off) under a benchmark agent, one project per conversation / question / dataset. Every question is a normal permission-checked `POST /api/memories/search` (`top_k = 50`, project filter).
* **Configurations.** This system in `hybrid` (default) and `semantic` mode. Baselines over exactly the same texts: **BM25** (Okapi, k1 = 1.5, b = 0.75) and a **plain vector store** — `all-MiniLM-L6-v2` embeddings ranked by cosine similarity, which is what a default Chroma-based memory does. LoCoMo additionally reports keyword-only and a recency baseline.
* **Scoring.** LoCoMo: Recall@k = share of the annotated evidence turns retrieved (the LoCoMo paper's metric). LongMemEval: the official retrieval code (`src/retrieval/eval_utils.py`, `run_retrieval.py`) ported line by line — user turns as items, `recall_all@k`, `ndcg_any@k`, turn→session mapping, abstention questions and questions without user-side evidence skipped. BEIR: nDCG@10 with graded relevance (linear gain, as `trec_eval` / `pytrec_eval`).
* **Validation of the scoring.** The local `all-MiniLM-L6-v2` baseline reproduces its published BEIR scores exactly (SciFact 0.645 vs 0.645 in MTEB, NFCorpus 0.316 vs 0.316), and this system's semantic mode reproduces bge-small-en-v1.5's published scores (0.717 vs 0.713, 0.342 vs 0.343). The metric code and the API pipeline therefore do not distort the results.
* **Retrieval quality first, answer accuracy separately.** Published memory-layer comparisons grade answers written by an LLM with an LLM judge (GPT-4o / GPT-4o-mini). This section reports retrieval quality — the evidence an answering model receives. Answer accuracy was measured separately with a local open model (§5); because the answerer and judge differ from the published ones, those accuracies rank this project's configurations against each other and are never put in one comparison with the published GPT-4o-mini numbers.

### 3.2 LoCoMo (run `20260928-044654`)

| Configuration | Recall@5 | Recall@10 | Recall@25 | Recall@50 | Hit@1 | MRR | nDCG@10 |
|---|---|---|---|---|---|---|---|
| This system — hybrid (default) | **0.582** | **0.673** | **0.789** | **0.852** | **0.392** | **0.511** | **0.523** |
| This system — semantic only | 0.561 | 0.657 | 0.783 | 0.848 | 0.352 | 0.478 | 0.494 |
| This system — keyword only (all terms) | 0.119 | 0.121 | 0.122 | 0.123 | 0.118 | 0.126 | 0.118 |
| BM25 baseline | 0.523 | 0.593 | 0.669 | 0.719 | 0.345 | 0.450 | 0.463 |
| Plain vector store (all-MiniLM-L6-v2 + cosine) | 0.397 | 0.502 | 0.637 | 0.729 | 0.212 | 0.326 | 0.344 |
| Recency baseline (last k turns) | 0.002 | 0.010 | 0.034 | 0.078 | 0.001 | 0.005 | 0.004 |
| *Published: DRAGON retriever over dialog turns (LoCoMo paper, Table 3)†* | *0.588* | *0.675* | *0.799* | *0.848* | — | — | — |

Recall@10 by question category:

| Configuration | single-hop | temporal | multi-hop | open-domain |
|---|---|---|---|---|
| This system — hybrid (default) | **0.733** | **0.736** | **0.488** | **0.473** |
| This system — semantic only | 0.711 | 0.720 | 0.485 | **0.473** |
| BM25 baseline | 0.699 | 0.688 | 0.277 | 0.267 |
| Plain vector store (all-MiniLM-L6-v2 + cosine) | 0.570 | 0.562 | 0.309 | 0.266 |

Search latency (hybrid, `top_k = 50`): client p50 83.2 ms / p95 186.3 ms, server p50 61.7 ms / p95 160.9 ms. Retrieved top-10 context ≈ 441 tokens per question vs ≈ 21 475 tokens for a whole conversation. Ingestion 19.2 turns/s. The retrieval figures are identical to the first LoCoMo run on the main instance (`locomo-20260927-200109`) — retrieval is deterministic.

† DRAGON was evaluated on the full 50-conversation LoCoMo set; the 10 released conversations used here are the subset the Mem0 paper used.

### 3.3 LongMemEval (run `20260928-045828`)

LongMemEval_S, all 419 questions the official protocol evaluates (500 in total; 30 abstention questions and those whose evidence is only in assistant turns are skipped, as in the official code). Each question's history — ≈ 244 user messages across ≈ 48 sessions — was stored in its own project (102 229 memories in total).

Turn level (official metric: Recall@k = *all* evidence messages in the top k):

| Configuration | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | Recall@50 |
|---|---|---|---|---|---|
| This system — hybrid (default) | **0.637** | 0.663 | **0.807** | **0.708** | **0.974** |
| This system — semantic only | **0.637** | 0.656 | 0.802 | 0.700 | **0.974** |
| BM25 baseline | 0.628 | **0.664** | 0.742 | 0.693 | 0.840 |
| Plain vector store (all-MiniLM-L6-v2 + cosine) | 0.511 | 0.570 | 0.711 | 0.623 | 0.936 |
| *Published: Stella V5 1.5B on LongMemEval_M, round level (paper Table 3)‡* | *0.582* | *0.481* | *0.692* | *0.512* | — |
| *Published: same + LLM-extracted fact keys‡* | *0.644* | *0.498* | *0.784* | *0.536* | — |

Session level (sessions of the ranked messages):

| Configuration | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 |
|---|---|---|---|---|
| This system — hybrid (default) | **0.776** | **0.697** | 0.907 | **0.725** |
| This system — semantic only | 0.773 | 0.690 | **0.909** | 0.719 |
| BM25 baseline | 0.704 | 0.677 | 0.783 | 0.699 |
| Plain vector store (all-MiniLM-L6-v2 + cosine) | 0.690 | 0.611 | 0.840 | 0.647 |
| *Published: Stella V5 1.5B on LongMemEval_M, session level‡* | *0.706* | *0.617* | *0.783* | *0.638* |

Turn Recall@10 by question type:

| Configuration | knowledge-update | multi-session | single-session-assistant | single-session-preference | single-session-user | temporal-reasoning |
|---|---|---|---|---|---|---|
| This system — hybrid (default) | **0.958** | **0.711** | **1.000** | **0.800** | 0.953 | 0.732 |
| This system — semantic only | **0.958** | **0.711** | **1.000** | **0.800** | 0.938 | 0.724 |
| BM25 baseline | 0.931 | 0.562 | 0.800 | 0.500 | **0.969** | **0.748** |
| Plain vector store (all-MiniLM-L6-v2 + cosine) | 0.903 | 0.545 | **1.000** | 0.767 | 0.953 | 0.614 |

Search latency (hybrid): client p50 83.9 ms / p95 188.7 ms. Ingestion 23.2 memories/s.

‡ The published retrieval numbers use LongMemEval_**M** (≈ 500 sessions, 1.5 M tokens per question — about ten times the haystack of the S setting used here) and a 1.5 B-parameter retriever, so they are a reference point, not a like-for-like comparison.

### 3.4 BEIR (run `20260928-043112`)

| Configuration | SciFact nDCG@10 | SciFact Recall@50 | NFCorpus nDCG@10 | NFCorpus Recall@50 |
|---|---|---|---|---|
| This system — hybrid (default) | **0.718** | **0.930** | **0.359** | **0.263** |
| This system — semantic only | 0.717 | **0.930** | 0.342 | 0.255 |
| BM25 baseline | 0.666 | 0.875 | 0.312 | 0.212 |
| Plain vector store (all-MiniLM-L6-v2 + cosine) | 0.645 | 0.892 | 0.316 | 0.251 |

Published nDCG@10 on the same test sets (not re-run here):

| Retriever | Source | SciFact | NFCorpus |
|---|---|---|---|
| BM25 (Elasticsearch) | BEIR paper | 0.665 | 0.325 |
| DPR | BEIR paper | 0.318 | 0.189 |
| ANCE | BEIR paper | 0.507 | 0.237 |
| TAS-B | BEIR paper | 0.643 | 0.319 |
| ColBERT | BEIR paper | 0.671 | 0.305 |
| BM25 + cross-encoder re-ranker | BEIR paper | 0.688 | 0.350 |
| all-MiniLM-L6-v2 (Chroma's default embedder) | MTEB | 0.645 | 0.316 |
| OpenAI text-embedding-ada-002 | MTEB | 0.727 | 0.370 |
| OpenAI text-embedding-3-small (Mem0's default embedder) | MTEB | 0.734 | 0.383 |
| bge-small-en-v1.5 (this system's model) | MTEB | 0.713 | 0.343 |
| **This system — hybrid** | measured | **0.718** | **0.359** |

Search latency (hybrid, client p50 / p95): SciFact 108.7 / 197.2 ms, NFCorpus 90.1 / 179.8 ms. Ingestion ≈ 13.8 documents/s (scientific abstracts are long; embedding dominates).

### 3.5 Comparison with published results of other memory layers

Figures of other systems are **quoted from their papers — they were not re-run here**, and most of them measure end-to-end answer accuracy with GPT-4o-mini. This project's answer accuracy (§5) was measured with a local 8B model on a sample, so it is shown for orientation only.

LoCoMo (Mem0 paper, Table 2 — GPT-4o-mini answers and judge, hosted services):

| System | Answer accuracy J (LLM judge) | Search p50 | Search p95 | Memory tokens |
|---|---|---|---|---|
| Full context (no memory layer) | 72.90 % | — | — | 26 031 |
| Mem0 with graph (Mem0ᵍ) | 68.44 % | 476 ms | 657 ms | 3 616 |
| Mem0 | 66.88 % | 148 ms | 200 ms | 1 764 |
| Zep | 65.99 % | 513 ms | 778 ms | 3 911 |
| Best RAG configuration (k = 2, 256-token chunks) | 60.97 % | 255 ms | 699 ms | — |
| LangMem | 58.10 % | 17 990 ms | 59 820 ms | 127 |
| OpenAI memory | 52.90 % | — | — | 4 437 |
| A-Mem | 48.38 % | 668 ms | 1 485 ms | 2 520 |
| **This system (hybrid)** | 72.6 %‡ | **83 ms** | **186 ms** | **≈ 441** (top-10 retrieved) |

‡ Gemma 4 8B (local) as answerer and judge, 135 sampled questions (§5) — comparable with the configurations run
in §5, **not** with the GPT-4o-mini rows above.

LongMemEval_S (answer accuracy with an LLM judge):

| System | Source | Accuracy | Context tokens | Latency |
|---|---|---|---|---|
| GPT-4o, oracle (evidence sessions only) | LongMemEval paper | 87.0 % | — | — |
| Zep + GPT-4o | Zep paper, Table 2 | 71.2 % | 1.6 k | 2.58 s end-to-end |
| Zep + GPT-4o-mini | Zep paper, Table 2 | 63.8 % | 1.6 k | 3.20 s end-to-end |
| GPT-4o, full context (no memory layer) | Zep paper, Table 2 | 60.2 % | 115 k | 28.9 s end-to-end |
| GPT-4o-mini, full context (no memory layer) | Zep paper, Table 2 | 55.4 % | 115 k | 31.3 s end-to-end |
| **This system (hybrid)** | this run | *not measured* — retrieval: all evidence in the top 10 for **80.7 %** of questions, at least one evidence message for **93.8 %** | top-10 messages | search p50 84 ms |

Evaluation dimensions covered:

| Dimension | Mem0 paper (Mem0, Zep, LangMem, A-Mem, OpenAI) | Zep paper | LoCoMo / LongMemEval / BEIR papers | This project |
|---|:-:|:-:|:-:|:-:|
| Evidence retrieval quality (Recall@k, nDCG) |  |  | ✔ | ✔ |
| End-to-end answer accuracy (LLM judge) | ✔ | ✔ | ✔ (LoCoMo, LongMemEval) | ✔ (LoCoMo sample, local judge, §5) |
| Search latency (p50 / p95) | ✔ | end-to-end only |  | ✔ |
| Context size sent to the LLM | ✔ | ✔ |  | ✔ |
| Cross-team knowledge reuse |  |  |  | ✔ (§2.1, §2.3) |
| Access-control leakage under attack |  |  |  | ✔ (§2.4: 0 / 140) |
| Audit coverage of denials |  |  |  | ✔ (§2.4: 100 %) |

### 3.6 Discussion

* **Hybrid search is the best configuration on every benchmark's headline metric.** Against the two
  baselines run on exactly the same texts:

  | Headline metric | Hybrid | BM25 | Plain vector store |
  |---|---|---|---|
  | LoCoMo Recall@10 | **0.673** | 0.593 | 0.502 |
  | LongMemEval turn Recall@10 | **0.807** | 0.742 | 0.711 |
  | LongMemEval session Recall@10 | **0.907** | 0.783 | 0.840 |
  | SciFact nDCG@10 | **0.718** | 0.666 | 0.645 |
  | NFCorpus nDCG@10 | **0.359** | 0.312 | 0.316 |

  The margins are largest where questions paraphrase the evidence: LoCoMo multi-hop
  (0.488 vs BM25 0.277), LongMemEval multi-session
  (0.711 vs 0.562) and preference questions (0.800 vs 0.500).
* **Where it is not ahead.** BM25 is marginally better on LongMemEval NDCG@5 (0.664 vs 0.663) and on
  the question types that repeat exact names and dates — single-session-user (0.969 vs 0.953)
  and temporal reasoning (0.748 vs 0.732). The keyword part of hybrid search only
  rewards memories that contain *every* query term, which long natural questions rarely satisfy, so its gain over pure
  embeddings is consistent but small (+0.016 LoCoMo, +0.005 LongMemEval,
  +0.001 SciFact, +0.018 NFCorpus). A BM25-weighted keyword ranking fused with the embedding
  ranking is the obvious next improvement.
* **On par with the published retrievers.** On LoCoMo hybrid search is within ±0.01 of DRAGON at every
  published cut-off; on BEIR it is above BM25, ColBERT and the BM25 + cross-encoder pipeline on both datasets and
  0.016 / 0.024 below OpenAI's `text-embedding-3-small` (Mem0's default embedder) — with a 33 M-parameter
  model running locally on a laptop CPU, no API key and no data leaving the server. The LongMemEval paper's numbers use a
  ten times larger haystack (M), so this run's higher recall (0.807 vs 0.692) is **not** evidence of a better retriever.
* **The scoring is verified.** The local all-MiniLM baseline reproduces its published BEIR scores exactly, the
  semantic mode reproduces bge-small's published scores, and the LongMemEval metric port matches the official functions to
  machine precision on 3 000 randomised rankings.
* **Latency stays flat with scale.** Median search latency was 83–109 ms (`top_k = 50`) on collections from
  5,882 to 102,229 memories, in the range of the fastest published memory layer
  (Mem0: 148 / 200 ms) and well below Zep (513 / 778 ms). Published figures include network round trips to hosted services;
  these do not, so this shows the design is not a bottleneck rather than a head-to-head win.
* **Context is small and ingestion needs no LLM.** The top-10 LoCoMo context is ≈ 441 tokens, about 2 % of a whole
  conversation. Knowledge is stored as written with one embedding per item (23.2 memories/s on a CPU;
  102,229 LongMemEval memories in 73 minutes), whereas Mem0 and Zep call an LLM on ingestion to
  extract facts or graph edges. The price is that nothing is consolidated or summarised — the LongMemEval paper shows
  LLM-extracted fact keys add ≈ 0.09 Recall@10, which is a clear direction for future work.
* **Better retrieval gives more correct answers.** With the same local answering model, answers grounded in this
  system's memories were judged correct for 72.6 % of sampled LoCoMo questions, against 67.4 % with BM25 and 54.8 %
  with a plain vector store (§5). The J figures of Mem0, Zep and the others were produced with GPT-4o-mini, so they are
  not directly comparable with these.
* **What the other systems do not measure.** None of the published memory-layer evaluations test multi-team access
  control, leakage under similarity attacks or auditability — this project's main contribution (§2.4: 0 of 140
  unauthorised attempts leaked, 100 % of denials audited).

References for the published figures: Maharana et al. (2024) LoCoMo, Table 3; Chhikara et al. (2025) Mem0, Table 2;
Rasmussen et al. (2025) Zep, Tables 1–3 (Zep also reports 94.8 % vs MemGPT's 93.4 % on Deep Memory Retrieval);
Wu et al. (2025) LongMemEval, Table 3 and Figure 3; Thakur et al. (2021) BEIR, Table 2; MTEB results repository
(`embeddings-benchmark/results`).

## 4. Where authorisation happens (controlled experiments)

`evaluation/ablation_filtering.py` (run `ablation-20260929-170548`, archived in `evaluation-runs/`) tests the
paper's claims about the placement of authorisation, with the system's embedding model and exact cosine search.
Ten teams; a share rho of every other team's memories is private. *LoCoMo-MT*: each LoCoMo conversation is one
team (topically disjoint knowledge). *SciFact-MT*: BEIR SciFact documents assigned to teams at random (topically
overlapping knowledge). k = 10, three seeds.

| Corpus | rho | Recall@10 authorise-before-rank | rank-then-filter (n = k) | (n = 10k) | short lists, n = k | short lists, n = 10k | leaked without filter |
|---|---|---|---|---|---|---|---|
| LoCoMo-MT | 0.5 | 0.569 | 0.562 | 0.569 | 24.8 % | 0.0 % | 5.1 % |
| LoCoMo-MT | 1.0 | 0.584 | 0.562 | 0.583 | 32.4 % | 0.5 % | 10.2 % |
| SciFact-MT | 0.5 | 0.867 | 0.842 | 0.867 | 99.1 % | 0.0 % | 42.2 % |
| SciFact-MT | 1.0 | 1.000 | 0.870 | 1.000 | 100.0 % | 3.7 % | 83.3 % |

* A *short list* (fewer than k results) under rank-then-filter reveals that hidden memories similar to the query
  exist. Authorise-before-rank, which the system uses, always returns k results and the best recall.
* **Collection-statistics side channel**: ranking the visible memories with BM25 whose statistics include hidden
  memories, and removing only hidden memories of one other team, changed the scores of visible results in
  100.0 %, their top-10 order in 36.9 % and the top-10 set in
  11.7 % of 1,200 question-seed pairs. The system's scores are document-local and unaffected.
* **Filtered HNSW in Qdrant** (all 5 882 vectors indexed, 500 queries): no unreadable result, top-10 agreement with
  exact authorise-before-rank 100.0 % (rho 0.5) and 100.0 % (rho 1), median latency
  1.5 / 1.2 ms filtered vs 1.4 / 1.3 ms unfiltered.
* At SciFact-MT rho = 1 only 81 question-seed pairs have readable evidence; treat that row as indicative.

## 5. Answer accuracy with an LLM judge (LoCoMo)

The Mem0 paper's protocol: an LLM answers each LoCoMo question from the memories a system retrieves, and an LLM judge
labels the answer CORRECT or WRONG against the gold answer (adversarial category excluded). One substitution: instead
of hosted GPT-4o-mini, **Gemma 4 8B** (`gemma4:latest`, 4-bit, Ollama, temperature 0, thinking off) runs on the laptop
as both answerer and judge. The answerer gets the top-10 memories with their dates; the judge prompt, like Mem0's,
accepts different wording and date formats.

* **Sample.** CPU generation takes ≈ 25–40 s per answer, so a stratified random sample of 200 of the 1,540
  answerable questions (proportional to category, seed 2026) was drawn in random order, and the first **135** were
  evaluated (73 single-hop, 29 temporal, 28 multi-hop, 5 open-domain) before the run was stopped for power reasons.
  Because the order is random, the 135 are themselves a random sample.
* **Paired design.** Every question is answered under four contexts — this system's hybrid search, BM25, the plain
  vector store, and no memory — so configurations are compared on identical questions (exact McNemar test).
  Intervals are 95 % Wilson intervals.

| Context given to the answerer | Accuracy J | 95 % CI | Mean context tokens | J when evidence retrieved | Evidence retrieved | McNemar vs this system |
|---|---|---|---|---|---|---|
| **This system (hybrid)** | **72.6 %** (98/135) | 64.5–79.4 % | 513 | 81.9 % | 77.8 % | — |
| BM25 | 67.4 % (91/135) | 59.1–74.7 % | 441 | 80.8 % | 73.3 % | 20 vs 13 questions, p = 0.296 |
| Plain vector store | 54.8 % (74/135) | 46.4–63.0 % | 445 | 81.3 % | 59.3 % | 30 vs 6 questions, p < 0.001 |
| No memory | 0.0 % (0/135) | 0.0–2.8 % | 0 | — | — | 98 vs 0 questions, p < 0.001 |

*Evidence retrieved*: at least one annotated evidence turn among the ten memories. *x vs y questions*: answered
correctly only with this system's context / only with the other context.

By category (questions): single-hop (73) 87.7 / 83.6 / 61.6 %, temporal (29) 37.9 / 37.9 / 37.9 %, multi-hop (28)
67.9 / 53.6 / 53.6 %, open-domain (5) 80 / 80 / 60 % for hybrid / BM25 / plain vector store.

* **Significance.** The lead over the plain vector store is significant (p < 0.001); the lead over BM25 is not at
  this sample size (p = 0.296).
* **The difference is retrieval.** When evidence was in the context, the three retrievers gave almost the same
  accuracy (81.9 / 80.8 / 81.3 %); this system put evidence into the context more often. Without memory the model
  declined or guessed wrongly on every question.
* **Judge audit.** 60 randomly drawn judgments were re-labelled by hand with the judge's rubric
  (`audit_labels.json`): agreement 86.7 %, Cohen's κ = 0.71; the judge was more lenient in 6 cases and stricter in 2,
  so absolute accuracies are probably a few points high. The judge never sees which context produced an answer, so
  this bias should not differ between contexts.
* **Not comparable with published J.** Answerer, judge and sample differ from the Mem0 paper's, so these numbers rank
  the four contexts against each other only.

## 6. Threats to validity

* **Small, synthetic corpus.** 35 memories and 40 queries written for this project; absolute numbers
  (especially the near-perfect Hit@5) will not transfer to large, noisy real corpora. The lexical
  baseline's strong Recall@5 reflects the tiny corpus.
* **Label bias.** Queries and labels were written by the same author as the memories; paraphrasing
  reduces but does not remove lexical overlap.
* **Isolated baseline is an idealisation** of per-agent memory (perfect self-retrieval, no forgetting).
* **Re-derivation cost** is measured as one extra store; real re-derivation (an LLM re-reasoning,
  a human re-investigating) is more expensive, so the extractive-run time gap is a lower bound, and
  the LLM-run comparison does not measure answer *correctness*.
* **Single machine.** Latencies were measured on a laptop with all services co-located; they include
  Docker networking but not real network distances. The small CPU-only LLM is far slower and weaker
  than hosted models.
* **Public-benchmark comparisons.** Published numbers of other systems were not reproduced here; they
  come from different hardware, hosted services and sometimes larger data (DRAGON: 50 LoCoMo conversations;
  Stella: LongMemEval_M), and most measure answer accuracy rather than retrieval quality (§3.6). The
  baselines that *were* run (BM25, plain vector store) use exactly the same texts and questions. Evidence
  labels in LoCoMo are known to be incomplete for some questions, which lowers every configuration equally.
* **Answer accuracy.** Measured on 135 of 1,540 LoCoMo questions with one local 8B model as both answerer and
  judge; the judge was audited by hand (κ = 0.71) but not by a second model. Absolute accuracies depend on the
  answering model; the paired comparison between contexts is the result.
* **Benchmark instance.** The public benchmarks ran against a second API process with rate limiting disabled
  (same code, own database); latencies therefore exclude throttling but include the full permission-checked
  search path.

## 7. Reproducing

```bash
# public benchmarks on the isolated instance (see docs/setup.md §6)
docker compose exec -d backend sh /opt/evaluation/bench_instance.sh
docker compose exec -e HF_HUB_OFFLINE=0 backend python /opt/evaluation/locomo_benchmark.py --base-url http://127.0.0.1:8001 --keep
docker compose exec -e HF_HUB_OFFLINE=0 backend python /opt/evaluation/longmemeval_benchmark.py --base-url http://127.0.0.1:8001 --keep
docker compose exec -e HF_HUB_OFFLINE=0 backend python /opt/evaluation/beir_benchmark.py --base-url http://127.0.0.1:8001 --keep
docker compose exec backend sh /opt/evaluation/bench_instance.sh --drop

docker compose exec backend python /opt/evaluation/run_evaluation.py --scale 1000 --scale-checkpoints 100,250,500,1000
# with a local LLM (see docs/setup.md §4): LLM_PROVIDER=ollama LLM_MODEL=qwen2.5:0.5b OLLAMA_NUM_THREAD=4
docker compose exec backend python /opt/evaluation/run_evaluation.py --repeat 1

# answer accuracy (§5): retrieval in the container, answering and judging with Ollama on the host
docker compose exec -e HF_HUB_OFFLINE=0 backend python /opt/evaluation/locomo_qa_retrieve.py --base-url http://127.0.0.1:8001
python evaluation/locomo_qa_answer.py evaluation/results/locomo-qa-<run> --model gemma4:latest   # resumable
```

Options: `--top-k`, `--threshold`, `--repeat`, `--scale`, `--scale-checkpoints`, `--keep`, `--out`.
