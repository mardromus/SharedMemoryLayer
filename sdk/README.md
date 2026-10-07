# shared-memory-sdk

Python SDK that lets an AI agent use the **Shared Memory Layer**: store what it learns, find what other
teams' agents already know, and keep that knowledge up to date — with the server enforcing
authentication, RBAC, per-memory visibility and auditing.

```bash
pip install ./sdk          # only dependency: httpx
```

## Quick start

```python
from shared_memory import MemoryClient

client = MemoryClient(base_url="http://localhost:8000", api_key="sml_…")   # agent API key

created = client.remember(
    title="JWT authentication issue",
    content="Refresh token expired.",
    memory_type="learning",
    tags=["authentication", "jwt"],
)
print(created.memory.id, created.warnings.message)   # e.g. "Potential duplicate memories found."

results = client.search("authentication problems caused by expired tokens")
for r in results:
    print(f"{r.similarity:.3f}  [{r.team}] {r.title}  v{r.version}")
```

Responses are dictionaries with attribute access (`r.title` == `r["title"]`).

## Authentication

| Mode | Usage |
|---|---|
| Agent API key | `MemoryClient(url, api_key="sml_…")` — sent as `X-API-Key` |
| Agent JWT | `client.exchange_api_key()` — trades the key for a short-lived JWT (auto-refreshed) |
| Human user | `MemoryClient(url, username="qa.lead", password="…")` — logs in and refreshes transparently |

## API

| Method | Description |
|---|---|
| `remember(title, content, memory_type="knowledge", *, tags, visibility, sensitivity, project_id, importance, confidence, source, source_ref, metadata, expires_at, allowed_team_ids, allowed_agent_ids, parent_memory_id, reject_on_duplicate)` | Store a memory; returns `memory` + `warnings` (duplicates / conflicts) |
| `search(query, top_k=5, *, mode, similarity_threshold, memory_types, team_ids, agent_ids, project_ids, tags, created_after, created_before, include_expired, only_own)` | Permission-aware search → `SearchResults` (a list with `.query`, `.took_ms`, `.mode`). `mode`: `hybrid` (default: meaning + exact keywords such as error codes), `semantic`, `keyword` |
| `remember_many(items, *, skip_duplicates, detect_duplicates, stop_on_error)` | Bulk import (batched by 100); pair with `shared_memory.importers` |
| `get(memory_id)` | Retrieve one memory (raises `PermissionDeniedError` if not authorised) |
| `update(memory_id, *, change_description, expected_version, **fields)` | Update → new version |
| `archive(memory_id)` / `delete(memory_id)` | Archive (needs `memory:delete`) |
| `share(memory_id, team_ids=[…], agent_ids=[…])` / `unshare(memory_id, "team:<id>")` | Manage sharing grants |
| `versions(memory_id)` / `version(memory_id, n)` | Version history |
| `provenance(memory_id)` | Creator, team, source, versions, modifiers, access summary |
| `feedback(memory_id, rating, helpful=True, comment="")` | Rate usefulness (updates reliability) |
| `check_duplicates(title, content, tags)` | Duplicate/conflict pre-flight without storing |
| `run_task(task, *, top_k, store_result, project_id, only_own)` | Server-side memory-augmented LLM agent |
| `whoami()` | Current identity, roles and permissions |
| `request(method, path, json=, params=)` | Any other endpoint (e.g. administration) |

## Errors

All API errors raise subclasses of `MemoryAPIError` carrying `status_code`, `code`, `message`,
`request_id` and `details`:

| Exception | Status |
|---|---|
| `AuthenticationError` | 401 |
| `PermissionDeniedError` | 403 (e.g. `Access denied: Unauthorized team`) |
| `NotFoundError` | 404 |
| `ConflictError` | 409 (duplicate rejected with `reject_on_duplicate=True`, version mismatch) |
| `ValidationError` | 422 |
| `RateLimitError` | 429 after retries (`retry_after`) |
| `ServerError` | 5xx after retries |

The client retries automatically on `429` (honouring `Retry-After`), on `502/503/504` and on transport
errors (exponential back-off, `max_retries=3`), and refreshes expired JWTs once.

## Example: an agent that learns from other teams

```python
from shared_memory import MemoryClient

qa = MemoryClient("http://localhost:8000", api_key=QA_KEY)

task = "Write test cases for authentication failures caused by expired tokens"
known = qa.search(task, top_k=3)
if known:
    print("Reusing knowledge from:", {r.team for r in known})
else:
    solution = solve_from_scratch(task)                    # your agent's own reasoning
    qa.remember(f"Result: {task}", solution, memory_type="task_result", tags=["testing"])

# or let the server-side runtime do search -> LLM -> store in one call
out = qa.run_task(task, store_result=True)
print(out.answer, [m.title for m in out.memories_used])
```

A runnable multi-agent demonstration is in [`examples/demo_agents.py`](examples/demo_agents.py).

## Tests

```bash
pip install ./sdk[test] && pytest sdk/tests     # transport-level unit tests
```

End-to-end tests driving the SDK against the real API, PostgreSQL and Qdrant live in
`backend/tests/test_integration_sdk.py`.

## Importing existing documents

```python
from shared_memory import MemoryClient, importers

items = importers.load_path("./runbooks", tags=["runbook"], visibility="team")   # .md/.txt/.json/.csv, folders recurse
client.remember_many(items, skip_duplicates=True)                              # idempotent re-runs
```

Markdown is split into one memory per `##` section; JSON/CSV rows need `title` and `content`.

## Command line (`sml`)

Installed with the package. Uses `SML_URL` and `SML_API_KEY` (or `SML_USERNAME`/`SML_PASSWORD`).

```bash
sml whoami
sml search "authentication failures caused by expired tokens" -k 3 [--mode keyword] [--json]
sml remember "Cache TTL decision" "Product cache TTL is 5 minutes" --type decision --tags cache
sml import ./docs --tags handbook --skip-duplicates [--dry-run]
sml mcp-config --client cursor          # MCP client configuration for this server
```

## LLM tool definitions

```python
from shared_memory.tools import MemoryToolkit
toolkit = MemoryToolkit(client)
tools = toolkit.anthropic_tools()          # or toolkit.openai_tools()
result_json = toolkit.dispatch(tool_name, tool_arguments)
```

## MCP

Agents that speak the Model Context Protocol need no SDK at all — point them at `BASE/mcp` with an
`X-API-Key` header. See [docs/integrations.md](../docs/integrations.md).
