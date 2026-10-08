# Setup and operations

This page covers running the stack on a development machine. To put it on a server with HTTPS, see
[deployment.md](deployment.md).

## Requirements

* Docker Engine 24+ with Docker Compose v2 (Docker Desktop on Windows/macOS). x86-64 and ARM64 both work.
* ~3 GB free disk space for images (the embedding model is baked into the backend image).
* Python 3.10+ on the host **only** for `scripts/generate_env.py` (standard library) and, optionally,
  for running the SDK/evaluation from the host.
* Internet access during the first `docker compose build` (base images, Python/npm packages and the
  ~70 MB embedding model). At runtime nothing is downloaded.

## 1. Configure

```bash
python scripts/generate_env.py          # writes .env with random secrets and prints the admin password
```

or copy `.env.example` to `.env` and fill in `POSTGRES_PASSWORD`, `QDRANT_API_KEY`, `JWT_SECRET`
(≥ 32 random characters), `ADMIN_PASSWORD` and `DEMO_USER_PASSWORD`. Compose refuses to start
if a required secret is missing.

If ports 8000 or 3000 are taken, set `BACKEND_PORT` / `FRONTEND_PORT` in `.env`.

## 2. Start

```bash
docker compose up --build
```

| URL | What |
|---|---|
| http://localhost:3000 | Dashboard (log in as `admin` with `ADMIN_PASSWORD`) |
| http://localhost:8000/docs | Swagger UI |
| http://localhost:8000/health | Health of database, vector DB, embeddings and LLM |

On first start the backend applies migrations, creates the roles/permissions and the admin account,
creates the Qdrant collection and (with `SEED_DEMO_DATA=true`) seeds the demonstration organisation:
4 teams, 4 agents, 3 projects, 5 human users (team managers `dev.lead`, `qa.lead`, `sec.lead`,
`support.lead` and the read-only `auditor`, password `DEMO_USER_PASSWORD`) and 20 memories including
a three-version decision, a confidential Security incident, a cross-team share, a flagged conflict and
an expired memory. Every seeded memory goes through the normal API service layer.

To start without demo data set `SEED_DEMO_DATA=false` before the first start (or reset, below).

## 3. Using agents

Demo agents have no API key until you issue one: **Agents → New key** in the dashboard, or

```bash
curl -X POST http://localhost:8000/api/agents/<agent-id>/api-keys -H "Authorization: Bearer <admin jwt>" \
     -H "Content-Type: application/json" -d '{"name": "laptop"}'
```

The key is shown once (or use **Connect an agent** in the dashboard, which also produces ready-made
MCP / SDK / CLI snippets — see [integrations.md](integrations.md)). Then:

```python
from shared_memory import MemoryClient
client = MemoryClient("http://localhost:8000", api_key="sml_…")
client.remember("JWT authentication issue", "Refresh token expired.", memory_type="learning")
print(client.search("authentication problems caused by expired tokens"))
```

See [sdk/README.md](../sdk/README.md).

## 4. Optional: local LLM with Ollama

```bash
docker compose --profile llm up -d ollama
docker compose exec ollama ollama pull qwen2.5:0.5b
# .env
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:0.5b
OLLAMA_NUM_THREAD=4
docker compose --profile llm up -d backend
```

`OLLAMA_NUM_THREAD` matters on laptops and VMs: on the development machine (12-core ARM64 Docker
VM) Ollama's default thread count produced 0.26 tokens/s, while 4 threads produced ≈70 tokens/s.
Larger models (e.g. `llama3.2:3b`) give better answers at the cost of latency.

Hosted providers: `LLM_PROVIDER=openai` + `OPENAI_API_KEY` (+ optional `OPENAI_BASE_URL` for any
OpenAI-compatible server) or `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`. Without an LLM
(`LLM_PROVIDER=none`, default) the agent runtime returns an extractive synthesis of the retrieved
memories and says so.

Embeddings can likewise be switched (`EMBEDDING_PROVIDER=ollama|openai`, `EMBEDDING_MODEL=…`); after
changing the model run `POST /api/admin/reindex?recreate=true`.

## 5. Tests

```bash
# backend: unit + security + integration (real PostgreSQL test DB, Qdrant test collection, real embeddings)
docker compose exec backend pytest

# SDK unit tests
docker compose exec -w /opt/sdk backend python -m pytest -p no:cacheprovider tests

# TypeScript SDK (host, Node 18+): unit tests, plus a live test when SML_URL/SML_API_KEY are set
cd sdk-js && npm install && npm test

# final acceptance scenario against the running stack
docker compose exec backend python /opt/scripts/acceptance_test.py

# secret scan (host)
python scripts/check_secrets.py
```

The test-suite uses the `<POSTGRES_DB>_test` database (created by
`infrastructure/docker/postgres/init-test-db.sh`) and the `test_memories` Qdrant collection, so it
never touches demo data.

## 6. Evaluation benchmark

```bash
docker compose exec backend python /opt/evaluation/run_evaluation.py              # ≈ 1 minute
docker compose exec backend python /opt/evaluation/run_evaluation.py --scale 1000 # + latency vs corpus size
```

Reports are written to `evaluation/results/<run-id>/` (`results.md`, `results.json`, `per_query.csv`).

Public benchmarks (LoCoMo, LongMemEval, BEIR — see [evaluation.md §3](evaluation.md)). They download
the public datasets on first run and ingest up to ~100 000 memories, so run them against the
**isolated benchmark instance**: a second API process from the same image with its own database and
Qdrant collection, which keeps the demo organisation and its audit log untouched.

```bash
docker compose exec -d backend sh /opt/evaluation/bench_instance.sh      # API on 127.0.0.1:8001 inside the container
docker compose exec -e HF_HUB_OFFLINE=0 backend python /opt/evaluation/beir_benchmark.py --base-url http://127.0.0.1:8001 --keep         # ≈ 15 min
docker compose exec -e HF_HUB_OFFLINE=0 backend python /opt/evaluation/locomo_benchmark.py --base-url http://127.0.0.1:8001 --keep       # ≈ 11 min
docker compose exec -e HF_HUB_OFFLINE=0 backend python /opt/evaluation/longmemeval_benchmark.py --base-url http://127.0.0.1:8001 --keep  # ≈ 2 h (--sample 100: ≈ 25 min)
docker compose exec backend sh /opt/evaluation/bench_instance.sh --drop  # stop it, drop its database and collection
```

`HF_HUB_OFFLINE=0` lets the scripts download the `all-MiniLM-L6-v2` model used for the plain-vector-store
baseline (`--dense-baseline ''` skips it). Restarting the backend container stops the benchmark instance.
Without `--base-url` the scripts run against the main API and delete what they created afterwards; if
such a run is interrupted, `python /opt/scripts/cleanup_benchmark.py` removes its leftover data.

## 7. Operations

| Task | Command |
|---|---|
| Logs | `docker compose logs -f backend` (JSON lines with `request_id`) |
| Metrics | `curl localhost:8000/metrics` |
| Consistency check | `GET /api/admin/consistency` (admin) |
| Rebuild vector index | `POST /api/admin/reindex` (admin; `?recreate=true` drops the collection first) |
| Backup | `docker compose exec postgres pg_dump -U sml shared_memory > backup.sql` (vectors can be rebuilt from PostgreSQL) |
| Reset everything | `docker compose down -v` (deletes volumes) then `docker compose up --build` |

## 8. Local development without Docker (optional)

```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+psycopg://sml:<pw>@localhost:5432/shared_memory JWT_SECRET=… QDRANT_URL=http://localhost:6333 EMBEDDING_CACHE_DIR=./models
alembic upgrade head && uvicorn app.main:app --reload
cd frontend && npm install && VITE_API_TARGET=http://localhost:8000 npm run dev   # http://localhost:5173
```

(PostgreSQL and Qdrant ports are not published by default; add `ports:` entries in a
`docker-compose.override.yml` for this workflow.)

## Troubleshooting

* **`POSTGRES_PASSWORD is not set`** – run `python scripts/generate_env.py`.
* **Port already allocated** – change `BACKEND_PORT` / `FRONTEND_PORT` in `.env`.
* **Health shows `vector_db: down`** – check `docker compose logs qdrant`; search falls back to keyword mode meanwhile.
* **Changed passwords in `.env` after the first start** – the database keeps the old password; run `docker compose down -v` to re-initialise.
* **Docker Desktop on Windows fails with "The file cannot be accessed by the system"** – a stale socket file from an unclean shutdown; quit Docker Desktop, rename `%LOCALAPPDATA%\Docker\run` (and `%LOCALAPPDATA%\docker-secrets-engine` if named in the error) and start it again.
