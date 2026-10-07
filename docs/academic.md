# Academic documentation

*B.Tech final-year project — Shared Memory Layer for Cross-Team AI Agents*

## Abstract

Large-language-model (LLM) agents are increasingly deployed as autonomous workers inside
organisations — writing code, testing releases, reviewing security and answering customers. Each
agent, however, usually keeps its own short-lived or private memory. Agents belonging to different
teams therefore repeat investigations, reach inconsistent decisions and cannot benefit from what
another team's agent has already learned. This project designs and implements a **secure shared
memory layer**: a service through which autonomous agents store knowledge (decisions, learnings,
incidents, solutions …), retrieve it by meaning with embedding-based semantic search, update it with
full version history and share it across team boundaries — while every access is authenticated,
authorised by role-based access control and per-memory visibility rules, and recorded in an
append-only audit log. The system combines PostgreSQL (metadata, versions, permissions, audit),
Qdrant (vector search with access-control filters pushed into the index), a FastAPI service, a Python
SDK for agents, an optional LLM agent runtime and a React dashboard. A reproducible benchmark measures
retrieval quality, latency, knowledge reuse, duplicate work and resistance to unauthorised access by
comparing agents with isolated memories against agents that use the shared layer.

## Problem statement

In multi-team organisations, AI agents operate with **isolated memory**:

1. *Duplicated work* — an agent re-investigates a problem another team's agent already solved.
2. *Repeated reasoning* — expensive LLM reasoning is spent re-deriving known facts.
3. *Information silos* — knowledge is trapped in one team's agent or conversation history.
4. *Inconsistent decisions* — two agents record contradicting facts (e.g. "database = MongoDB" vs.
   "database = PostgreSQL") and nobody notices.
5. *Inefficient collaboration* — point-to-point agent communication scales as O(n²) and has no
   governance.

Simply putting all memories into one vector store would solve sharing but break **confidentiality**:
semantic search returns whatever is similar, including a security team's confidential vulnerability
report. The problem is therefore to provide *persistent, organisation-wide, semantically searchable*
memory that is *secure by construction* and *auditable*.

## Objectives

1. **Secure knowledge sharing** — authenticated agent identities, RBAC and five visibility levels
   enforced at the API, database and vector-index level.
2. **Semantic retrieval** — embedding-based search with top-k, thresholds and metadata filters.
3. **Reduced redundant work** — make existing knowledge findable across teams; detect duplicates.
4. **Cross-team collaboration** — explicit sharing grants between teams and individual agents.
5. **Memory versioning** — immutable version history and provenance for every memory.
6. **Access control** — least privilege, configurable permissions, privilege-escalation protection.
7. **Auditability** — tamper-resistant log of every security-relevant operation, including denials.
8. **Scalability** — stateless API, vector index with payload indexes, replaceable components.

## Literature survey

| Work | Idea | Relation to this project |
|---|---|---|
| **Multi-agent systems** (Wooldridge 2009; Stone & Veloso 2000) | autonomous agents coordinating via communication, shared environments or blackboard architectures | the shared memory acts as a governed *blackboard*: agents coordinate indirectly through persistent knowledge rather than point-to-point messages |
| **AutoGen** (Wu et al., 2023) | framework for LLM agents that converse to solve tasks | conversation state is local to a group chat; no persistent, access-controlled memory across teams |
| **MetaGPT** (Hong et al., 2023) | role-based software-company agents with a shared message pool and SOPs | shows the value of shared artefacts between roles; the pool is per-run and has no per-role confidentiality |
| **CAMEL** (Li et al., 2023) | role-playing communicative agents | focuses on dialogue between two roles; memory is the dialogue itself |
| **Generative Agents** (Park et al., 2023) | memory stream with retrieval by recency, importance and relevance; reflection | inspires the importance/confidence scores and relevance-based retrieval; memories are private to each agent |
| **Retrieval-Augmented Generation** (Lewis et al., 2020) | condition generation on retrieved documents | the agent runtime is RAG over organisational memory, with authorisation applied *before* retrieval |
| **Dense retrieval / sentence embeddings** (Reimers & Gurevych 2019; Karpukhin et al. 2020; BGE, Xiao et al. 2023) | semantic similarity via learned embeddings | `BAAI/bge-small-en-v1.5` provides the 384-d embeddings |
| **Vector databases** (Qdrant, Milvus, pgvector; HNSW — Malkov & Yashunin 2018) | approximate nearest-neighbour search with payload filtering | Qdrant's filtered HNSW search lets the ACL be evaluated inside the index |
| **AI memory systems** (MemGPT — Packer et al. 2023; LangChain/LlamaIndex memory modules; Mem0) | hierarchical / long-term memory for a single agent or user | provide long-term memory but not multi-tenant, cross-team governance with RBAC, visibility, versioning and audit |
| **Mem0** (Chhikara et al., 2025), **Zep** (Rasmussen et al., 2025) | production memory layers: LLM-extracted facts (Mem0) or a temporal knowledge graph (Zep) per user/agent, evaluated on LoCoMo / DMR | the closest systems; they optimise answer accuracy for one user or agent and do not evaluate cross-team access control or auditability — measured comparison in [evaluation.md §3](evaluation.md#3-public-benchmarks-and-comparison-with-other-memory-layers) |
| **LoCoMo benchmark** (Maharana et al., 2024) | very long multi-session conversations with evidence-annotated questions | used as the neutral benchmark: this system's hybrid retrieval reaches Recall@10 0.673 (DRAGON: 0.675) |
| **LongMemEval** (Wu et al., 2025) | 500 questions over long chat-assistant histories (≈ 115k tokens each) with annotated evidence | run with the official retrieval protocol; compared with the retrievers in the paper and Zep's published results |
| **BEIR** (Thakur et al., 2021) and **MTEB** (Muennighoff et al., 2023) | zero-shot retrieval benchmark; embedding-model leaderboard on the same test sets | retrieval quality compared with BM25, neural retrievers and the embedding models Mem0 and Chroma use by default |
| **Hybrid lexical + dense retrieval** (BM25 — Robertson & Zaragoza 2009; DRAGON — Lin et al. 2023) | combine exact-term matching with embeddings | search merges PostgreSQL full-text hits with vector hits under one permission filter |
| **Model Context Protocol** (Anthropic, 2024) | open protocol for connecting LLM applications to tools and data | the `/mcp` endpoint makes the memory layer usable from Claude Code, Cursor, VS Code and Claude Desktop without code |
| **Access control** (RBAC — Sandhu et al. 1996; ABAC — NIST SP 800-162) | roles, permissions, attribute-based policies | the design combines RBAC (operations) with attribute-based per-object visibility |

## Research gap

Existing agent frameworks provide either **ephemeral shared context** (a group chat or message pool
for one task) or **private long-term memory** for a single agent. None of the surveyed systems offers a
**persistent organisational memory shared by independent agents of different teams** that
simultaneously (a) retrieves by meaning, (b) guarantees that semantic similarity never bypasses
authorisation, (c) keeps versions and provenance, (d) detects duplicates and contradictions for human
review and (e) audits every access. This project addresses that gap.

## Proposed solution

A layered service (see [architecture.md](architecture.md)):

```
AI agents → Agent gateway/API → Authentication → Authorisation (RBAC + visibility)
          → Memory manager → Semantic retrieval
          → Shared memory layer [vector DB | metadata DB | versions | access control | audit logs]
          → Admin dashboard
```

Key design decisions:

* **Authorisation pushed into retrieval** — the per-memory read rule is compiled into the Qdrant
  filter, so unauthorised memories are never scored; results are re-validated against PostgreSQL.
* **PostgreSQL as system of record, Qdrant as index** — one vector per memory, compensating actions
  and a re-index endpoint keep them consistent.
* **Versioning by snapshot** — every update creates an immutable version linked to its predecessor.
* **Quality & trust** — importance, confidence, reliability (from feedback) and human verification
  influence ranking; duplicates and contradictions are flagged, never silently merged or overwritten.
* **Least-privilege agent identities** — each agent has its own API keys and roles; humans can
  simulate agents only with the agent's permissions, and such actions are audited.

## Expected outcomes and how they are measured

| Outcome | Measured by (`evaluation/run_evaluation.py`) |
|---|---|
| Agents find knowledge created by other teams | Hit@k / MRR on cross-team queries, knowledge-reuse rate |
| Less duplicated work | duplicate-task rate (tasks whose knowledge existed but had to be re-derived) and server duplicate flags |
| Faster task completion | agent-runtime time + measured re-derivation cost, isolated vs. shared |
| Semantic > lexical retrieval | Hit@1 / MRR vs. a TF-IDF ranking over the same authorised memories |
| Interactive latency | client and server search latency (p50/p95/p99) and latency vs. corpus size |
| No leakage of restricted memories | adversarial direct/search/update/share/escalation attempts: blocked vs. leaked, audit coverage |
| Competitive with other memory layers on public benchmarks | `evaluation/locomo_benchmark.py`, `longmemeval_benchmark.py`, `beir_benchmark.py`: Recall@k / nDCG / MRR, search latency and context size vs. BM25, a plain vector store and the published Mem0, Zep, LangMem, A-Mem, DRAGON, Stella, BEIR and MTEB figures |

Measured results of the reference run are in [evaluation.md](evaluation.md).

## Future scope

* LLM/NLI-based contradiction detection and automatic summarisation of related memories.
* A cross-encoder re-ranker on top of the implemented hybrid (full-text + dense) retrieval.
* LoCoMo answer accuracy on all 1,540 questions with GPT-4o-mini as answerer and judge, for a like-for-like J score
  (measured so far with a local 8B model on 135 sampled questions; see [evaluation.md §5](evaluation.md)).
* Attribute-based policies (project, clearance level, time windows) and policy-as-code (OPA).
* Memory decay and consolidation (reflection, as in Generative Agents).
* Redis-backed distributed rate limiting, horizontal scaling, Qdrant sharding/replication.
* Federation between organisations with signed provenance.
* Framework adapters for AutoGen/LangGraph (MCP, Python/TypeScript SDKs and OpenAI/Anthropic tool
  definitions are implemented).

## References

1. Wu, Q. et al. *AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation.* arXiv:2308.08155, 2023.
2. Hong, S. et al. *MetaGPT: Meta Programming for a Multi-Agent Collaborative Framework.* arXiv:2308.00352, 2023.
3. Li, G. et al. *CAMEL: Communicative Agents for "Mind" Exploration of Large Language Model Society.* NeurIPS 2023.
4. Park, J. S. et al. *Generative Agents: Interactive Simulacra of Human Behavior.* UIST 2023.
5. Lewis, P. et al. *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks.* NeurIPS 2020.
6. Packer, C. et al. *MemGPT: Towards LLMs as Operating Systems.* arXiv:2310.08560, 2023.
7. Reimers, N., Gurevych, I. *Sentence-BERT.* EMNLP 2019.
8. Xiao, S. et al. *C-Pack: Packaged Resources To Advance General Chinese Embedding (BGE).* arXiv:2309.07597, 2023.
9. Malkov, Y., Yashunin, D. *Efficient and Robust Approximate Nearest Neighbor Search Using HNSW Graphs.* IEEE TPAMI, 2018.
10. Sandhu, R. et al. *Role-Based Access Control Models.* IEEE Computer, 1996.
11. Hu, V. et al. *Guide to Attribute Based Access Control (ABAC).* NIST SP 800-162, 2014.
12. Wooldridge, M. *An Introduction to MultiAgent Systems*, 2nd ed., Wiley, 2009.
13. Maharana, A. et al. *Evaluating Very Long-Term Conversational Memory of LLM Agents* (LoCoMo). ACL 2024, arXiv:2402.17753.
14. Chhikara, P. et al. *Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory.* arXiv:2504.19413, 2025.
15. Rasmussen, P. et al. *Zep: A Temporal Knowledge Graph Architecture for Agent Memory.* arXiv:2501.13956, 2025.
16. Robertson, S., Zaragoza, H. *The Probabilistic Relevance Framework: BM25 and Beyond.* Foundations and Trends in Information Retrieval, 2009.
17. Lin, S.-C. et al. *How to Train Your DRAGON: Diverse Augmentation Towards Generalizable Dense Retrieval.* Findings of EMNLP 2023, arXiv:2302.07452.
18. Anthropic. *Model Context Protocol specification.* https://modelcontextprotocol.io, 2024–2025.
19. Wu, D. et al. *LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory.* ICLR 2025, arXiv:2410.10813.
20. Thakur, N. et al. *BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models.* NeurIPS 2021 Datasets and Benchmarks, arXiv:2104.08663.
21. Muennighoff, N. et al. *MTEB: Massive Text Embedding Benchmark.* EACL 2023, arXiv:2210.07316.
