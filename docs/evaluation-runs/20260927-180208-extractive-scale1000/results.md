# Evaluation results — run 20260927-180208

*Measured against `http://localhost:8000` (server 1.0.0, embeddings `BAAI/bge-small-en-v1.5`, LLM provider `extractive`) on 2026-09-27T18:02:08 UTC. Client: Python 3.12.14.*

## Setup

- Corpus: **35 labelled memories** written by 4 team agents (dev: 10, qa: 8, sec: 9, support: 8); visibility {'organization': 28, 'team': 6, 'selected_teams': 1}.
- Queries: **40** paraphrased questions, 25 of which can only be answered with knowledge owned by another team.
- Top-k = 5; similarity threshold for "returned" results = 0.55; latency repeats = 3.

## Retrieval quality

| Metric | Isolated memory (baseline) | Shared memory (proposed) | Lexical TF-IDF on shared set |
|---|---|---|---|
| hit@1 | 0.350 | 0.975 | 0.850 |
| hit@5 | 0.375 | 1.000 | 1.000 |
| precision@5 | 0.075 | 0.235 | 0.245 |
| recall@5 | 0.338 | 0.963 | 0.988 |
| mrr | 0.358 | 0.983 | 0.921 |
| ndcg@5 | 0.338 | 0.957 | 0.927 |
| hit@k_cross_team_queries | 0.000 | 1.000 | 1.000 |
| search_precision_above_threshold | 0.103 | 0.243 | — |
| answered_correctly_rate | 0.350 | 0.975 | — |
| knowledge_reuse_rate | 0.000 | 0.675 | — |

## Retrieval latency (ms)

| Configuration | Side | n | mean | p50 | p95 | p99 |
|---|---|---|---|---|---|---|
| isolated | client | 120 | 107.3 | 100.15 | 168.51 | 223.57 |
| isolated | server | 120 | 86.78 | 80.77 | 142.42 | 185.38 |
| shared | client | 120 | 112.88 | 103.3 | 207.54 | 270.24 |
| shared | server | 120 | 91.92 | 83.36 | 162.02 | 211.94 |

Memory creation (validate + embed + duplicate/conflict detection + PostgreSQL + Qdrant + version + audit): mean 205.23 ms, p50 189.45 ms, p95 335.65 ms (n=35).

## Agent tasks: reuse, duplicate work and completion time

| Metric | Isolated memory | Shared memory |
|---|---|---|
| Tasks resolved from existing memory | 0.375 | 1.000 |
| Duplicate task rate (knowledge existed but was re-derived) | 0.625 | 0.000 |
| Re-stored memories flagged as duplicates by the server | 25 | 0 |
| Tasks that used another team's memory | 0 | 39 |
| Agent runtime per task, mean / p95 (ms) | 118.01 / 193.27 | 122.39 / 221.7 |
| Task completion incl. re-derivation, mean / p95 (ms) | 265.45 / 451.03 | 122.39 / 221.7 |

LLM provider during the run: `extractive`. Re-derivation is measured as one extra store of the re-derived knowledge (embedding, duplicate detection, persistence); real re-derivation by an LLM or engineer is far more expensive, so the completion-time gap is a lower bound.

## Security: unauthorised access attempts

7 protected memories (team/private/selected-teams); every agent outside the authorised teams attempted each access path.

| Attempt | Attempts | Blocked |
|---|---|---|
| direct_read | 20 | 20 |
| update | 20 | 20 |
| read_versions | 20 | 20 |
| share_to_self | 20 | 20 |
| semantic_search_title | 20 | 20 |
| semantic_search_content | 20 | 20 |
| escalation:create_team | 4 | 4 |
| escalation:create_agent | 4 | 4 |
| escalation:read_audit_log | 4 | 4 |
| escalation:list_users | 4 | 4 |
| escalation:reindex_vectors | 4 | 4 |
| **Total** | **140** | **140** |

Leaked: **0** · block rate **1.0000** · denials written to the audit log: **100 / 100** expected.

## Search latency vs. corpus size

| Memories in project | client p50 | client p95 | server p50 | server p95 |
|---|---|---|---|---|
| 100 | 98.12 | 131.52 | 80.25 | 111.69 |
| 250 | 111.79 | 254.37 | 89.57 | 152.05 |
| 500 | 108.38 | 150.39 | 89.72 | 129.86 |
| 1000 | 110.65 | 193.85 | 93.05 | 175.59 |

Bulk write latency during the scale test: mean 264.34 ms (n=1000).

## Method notes

- *Isolated* restricts retrieval to memories the asking agent created itself (`only_own=true`), modelling per-agent memory silos.
- *Lexical* ranks exactly the memories the agent may read with TF-IDF cosine similarity, so the difference between *lexical* and *shared* isolates the contribution of semantic embeddings.
- Ranking metrics use the top-k list irrespective of threshold; *search precision* and *answered correctly* only count results at or above the threshold.
- Query texts are paraphrases written for this benchmark; the corpus is small and synthetic, so absolute numbers should not be generalised.
