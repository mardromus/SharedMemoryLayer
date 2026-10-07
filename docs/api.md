# REST API

Interactive documentation is generated from the code:

* Swagger UI — `http://localhost:8000/docs` (or `http://localhost:3000/docs` through nginx)
* ReDoc — `http://localhost:8000/redoc`
* OpenAPI JSON — `http://localhost:8000/openapi.json`

Use the **Authorize** button in Swagger (OAuth2 password flow → `/api/auth/token`) or paste an agent
API key into the `X-API-Key` field.

## Authentication

| Header | Used by |
|---|---|
| `Authorization: Bearer <jwt>` | humans (after login) and agents (after `/api/auth/agent-token`) |
| `X-API-Key: sml_<prefix>_<secret>` | agents |

All error responses: `{"error": {"code", "message", "request_id", "details?"}}` (see [security.md](security.md#6-error-handling)).

## Endpoints

### Authentication
| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/register` | Create a user (admins: any role/team; public self-registration only if enabled, always READ_ONLY) |
| POST | `/api/auth/login` | Username/password → access + refresh token (also sets HttpOnly refresh cookie) |
| POST | `/api/auth/token` | OAuth2 password form (Swagger "Authorize") |
| POST | `/api/auth/agent-token` | Exchange an agent API key for a JWT |
| POST | `/api/auth/refresh` | Rotate the refresh token (body or cookie) |
| POST | `/api/auth/logout` | Revoke the refresh-token family |
| GET | `/api/auth/me` | Current principal, roles and effective permissions |

### Memories
| Method | Path | Permission | Description |
|---|---|---|---|
| POST | `/api/memories` | memory:create | Create (validate → embed → duplicate/conflict detection → store → version → audit) |
| GET | `/api/memories` | memory:read | Paginated listing with keyword `q` and filters (`memory_type`, `team_id`, `agent_id`, `project_id`, `visibility`, `status`, `verification_status`, `conflict_status`, `tag`, `include_expired`) |
| POST | `/api/memories/search` | memory:read | Hybrid (default), semantic or keyword search |
| POST | `/api/memories/bulk` | memory:create | Import up to 100 memories; per-item results (`created` / `skipped_duplicate` / `error`); `skip_duplicates`, `detect_duplicates`, `stop_on_error` |
| POST | `/api/memories/check` | memory:read | Duplicate/conflict pre-flight without storing |
| GET | `/api/memories/{id}` | memory:read + visibility | Retrieve (`include_expired=true` for expired) |
| PATCH | `/api/memories/{id}` | memory:update + owner/manager | Update → new version (`expected_version` for optimistic locking) |
| DELETE | `/api/memories/{id}` | memory:delete + owner/manager | Archive; `?permanent=true` (admin) deletes from both stores |
| POST | `/api/memories/{id}/restore` | admin | Restore an archived memory |
| GET | `/api/memories/{id}/versions` | read | Version history (newest first) |
| GET | `/api/memories/{id}/versions/{n}` | read | One version |
| GET | `/api/memories/{id}/provenance` | read | Creator, team, source, versions, modifiers, access summary/history |
| POST | `/api/memories/{id}/share` | memory:share + owner/manager | Grant teams/agents |
| DELETE | `/api/memories/{id}/share/{target}` | memory:share + owner/manager | Revoke (`team:<uuid>`, `agent:<uuid>` or `<uuid>`) |
| POST | `/api/memories/{id}/feedback` | read | Rate 1–5 (updates reliability) |
| POST | `/api/memories/{id}/verify` | memory:verify, human manager/admin | `verified` / `rejected` / `unverified` |

### Agents, teams, projects, users, roles
| Method | Path | Permission |
|---|---|---|
| POST / GET | `/api/agents` | agent:create / authenticated |
| GET / PATCH / DELETE | `/api/agents/{id}` | authenticated / agent:update / agent:delete (own team for managers) |
| PUT | `/api/agents/{id}/roles` | agent:update (managers: AGENT, READ_ONLY only) |
| POST / DELETE | `/api/agents/{id}/api-keys[/{key_id}]` | agent:update |
| POST / GET | `/api/teams` | team:create / authenticated (`include_inactive=true` to list deactivated teams) |
| GET / PATCH | `/api/teams/{id}` | authenticated / team:update (own team for managers) |
| GET / POST | `/api/projects` | authenticated / team:update |
| GET / PATCH | `/api/users[/{id}]` | user:manage |
| GET | `/api/roles`, `/api/permissions` | authenticated |
| PUT | `/api/roles/{id}/permissions` | role:manage |

### Governance, runtime and operations
| Method | Path | Description |
|---|---|---|
| GET | `/api/audit-logs` | Filter by `actor`, `actor_type`, `action`, `status`, `resource_type`, `resource_id`, `since`, `until`; paginated |
| GET | `/api/audit-logs/actions` | Distinct action names |
| GET | `/api/conflicts?status=open` | Conflict review queue |
| POST | `/api/conflicts/{id}/resolve` | `keep_new` / `keep_existing` / `keep_both` / `dismiss` |
| POST | `/api/agent-runtime/tasks` | Agent solves a task with shared memory as LLM context (`store_result`, `only_own`) |
| POST | `/api/playground/search` · `/tasks` · `/memories` · `/access-check` | Human simulates an agent (agent's permissions, audited `on_behalf_of`) |
| GET | `/api/dashboard/stats` | Live statistics |
| POST | `/api/admin/reindex` | Rebuild vectors from PostgreSQL (`recreate=true` to drop the collection) |
| GET | `/api/admin/consistency` | PostgreSQL rows vs. Qdrant points |
| GET | `/api/admin/config` | Non-secret runtime configuration |
| GET | `/health`, `/health/live`, `/metrics` | Health (503 if a dependency is down), liveness, Prometheus |
| POST/GET | `/mcp` | Model Context Protocol (Streamable HTTP, JSON responses, stateless); `X-API-Key` or Bearer required. Tools: `search_memory`, `remember`, `get_memory`, `update_memory`, `share_memory`, `memory_history`, `list_teams`, `whoami` — see [integrations.md](integrations.md) |

## Examples

### Create a memory (agent)

```bash
curl -X POST http://localhost:8000/api/memories \
  -H "X-API-Key: $DEV_AGENT_KEY" -H "Content-Type: application/json" \
  -d '{
        "title": "JWT authentication issue",
        "content": "Authentication failed because refresh tokens expired.",
        "memory_type": "learning",
        "visibility": "team",
        "tags": ["authentication", "jwt", "security"]
      }'
```

```json
{
  "memory": {"id": "8ff70cc9-…", "version": 1, "team": {"id": "…", "name": "Development"},
             "visibility": "team", "embedding_model": "BAAI/bge-small-en-v1.5", "…": "…"},
  "warnings": {"message": "Potential duplicate memories found.",
               "duplicates": [{"memory_id": "…", "title": "…", "similarity": 0.953, "team": "Development", "exact_match": false}],
               "conflicts": []}
}
```

### Semantic search

```bash
curl -X POST http://localhost:8000/api/memories/search \
  -H "X-API-Key: $QA_AGENT_KEY" -H "Content-Type: application/json" \
  -d '{"query": "authentication failures caused by expired tokens", "top_k": 5,
       "memory_types": ["learning", "incident"], "similarity_threshold": 0.55}'
```

```json
{
  "query": "authentication failures caused by expired tokens",
  "mode": "semantic",
  "total": 1,
  "took_ms": 48.2,
  "threshold": 0.55,
  "results": [
    {"memory_id": "8ff70cc9-…", "title": "JWT authentication fails when refresh tokens expire",
     "content": "…", "similarity": 0.815, "score": 0.822, "team": "Development", "memory_type": "learning",
     "confidence": 0.92, "version": 2, "cross_team": true, "…": "…"}
  ]
}
```

Search request fields: `query`, `mode` (`hybrid` default — semantic similarity plus a bonus for memories containing every query term, which catches exact identifiers such as error codes; `semantic`; `keyword`), `top_k` (1–50), `similarity_threshold` (0–1), `memory_types`,
`team_ids`, `agent_ids` (creator), `project_ids`, `tags`, `visibility`, `verification_status`,
`min_confidence`, `created_after`, `created_before`, `include_expired`, `include_archived` (admin),
`include_rejected`, `only_own`.

### Denied access

```bash
curl http://localhost:8000/api/memories/a47088a7-… -H "X-API-Key: $SUPPORT_AGENT_KEY"
# 403 {"error": {"code": "forbidden", "message": "Access denied: Unauthorized team", "request_id": "…"}}
```
