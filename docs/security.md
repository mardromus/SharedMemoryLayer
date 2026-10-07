# Security architecture

## 1. Threat model (summary)

| Threat | Mitigation |
|---|---|
| Agent reads another team's confidential memory | Per-memory visibility enforced server-side on every read, update, share, version and provenance call; returns 403 + audit record |
| Restricted memory leaks through semantic similarity | The ACL is compiled into the Qdrant filter, so unreadable memories are never scored; results are re-checked against PostgreSQL before they are returned |
| Duplicate/conflict warnings disclose unreadable memories | Duplicates are only searched among readable memories; conflicts with unreadable memories are flagged to administrators but only a count is returned to the caller |
| Stolen / forged tokens | HS256 JWTs with fixed algorithm list, issuer, `exp`, `nbf`, `jti`; `alg=none` and foreign signatures rejected; 15-minute lifetime |
| Refresh-token theft | Opaque 384-bit tokens stored as keyed hashes; rotated on every use; re-use of a rotated token revokes the entire token family |
| API-key leakage from the database | Keys are `sml_<prefix>_<secret>`; only a keyed SHA-256 digest is stored; plaintext shown once; revocable; expiring |
| Password cracking | Argon2id (argon2-cffi defaults), password policy (≥10 chars, 3 of 4 classes, not containing the username), constant-time comparison, dummy-hash timing equalisation |
| Brute force | Per-IP rate limit on auth endpoints (429 + `Retry-After`), account lockout after `MAX_FAILED_LOGINS` for `LOCKOUT_MINUTES`, API-key guessing is rate limited |
| Privilege escalation | Public registration disabled by default and always `READ_ONLY`; team managers may only assign `AGENT`/`READ_ONLY`; admins cannot demote/deactivate themselves; ADMIN keeps `role:manage` + `system:admin` |
| Impersonation | Body fields `agent_id` / `team_id` must match the authenticated principal; playground simulation always uses the *agent's* permissions and is audited with `on_behalf_of` |
| Unauthorised object access (IDOR) | Every object access goes through `can_read` / `can_modify`; UUID identifiers |
| SQL injection | SQLAlchemy ORM / bound parameters only; covered by a test |
| XSS | Titles/tags reject HTML markup; React escapes all output (no `dangerouslySetInnerHTML`); strict CSP on the SPA (`script-src 'self'`) |
| Malformed / oversized input | Pydantic validation (lengths, enums, UUIDs, tag regex, 8 KB metadata, configurable content limit), `client_max_body_size 1m` in nginx |
| Audit tampering | `audit_logs` protected by a PostgreSQL trigger that rejects `UPDATE`/`DELETE`; audit writes use an independent transaction so denied/failed operations are always recorded |
| Secret exposure | No secret has a default; `.env` is git-ignored; `scripts/check_secrets.py` scans the repository; errors never contain stack traces |
| Unauthenticated infrastructure | PostgreSQL and Qdrant are not published; Qdrant requires an API key |
| MCP as a side door | `/mcp` rejects requests without a valid API key/JWT (401) before the MCP transport runs; tools execute as the agent through the same services, so RBAC, visibility, rate limits and auditing are identical to REST |

## 2. Authentication

### Humans
`POST /api/auth/login` → `{access_token, refresh_token}`. The browser dashboard keeps the access token
in memory only; the refresh token is additionally set as an `HttpOnly; SameSite=Strict; Path=/api/auth`
cookie (`Secure` when `COOKIE_SECURE=true`, enforced in production).

### Agents
Each agent has its own identity and one or more API keys:

* `X-API-Key: sml_…` on every request, **or**
* `POST /api/auth/agent-token {"api_key": …}` → short-lived JWT + refresh token.

Suspending or deleting an agent immediately invalidates its keys and tokens (checked on every request).

## 3. Authorisation

### 3.1 RBAC

Permissions are rows in `permissions`, grouped into roles via `role_permissions`; users have one role,
agents have one or more roles (`agent_roles`). Changes via `PUT /api/roles/{id}/permissions` apply to
the next request.

| Permission | ADMIN | TEAM_MANAGER | AGENT | READ_ONLY |
|---|:-:|:-:|:-:|:-:|
| memory:create | ✔ | ✔ | ✔ | |
| memory:read | ✔ | ✔ | ✔ | ✔ |
| memory:update | ✔ | ✔ | ✔ | |
| memory:delete (archive) | ✔ | ✔ | | |
| memory:share | ✔ | ✔ | ✔ | |
| memory:verify | ✔ | ✔ | | |
| agent:create / update / delete | ✔ | ✔ (own team) | | |
| team:create | ✔ | | | |
| team:update | ✔ | ✔ (own team) | | |
| audit:read | ✔ | ✔ (own team's actors) | | |
| conflict:resolve | ✔ | ✔ (own team) | | |
| user:manage, role:manage, system:admin | ✔ | | | |

### 3.2 Memory visibility (object-level)

| Visibility | Readable by |
|---|---|
| `private` | the creating agent only (never shareable) |
| `team` | agents of the owning team + explicit team/agent grants |
| `selected_teams` | owning team + explicitly allowed teams |
| `selected_agents` | explicitly allowed agents (not the rest of the owning team) |
| `organization` | every authenticated principal (forbidden for `confidential`/`restricted`) |

Additional rules: archived → administrators only; expired → hidden unless `include_expired=true`;
modification (update/archive/share) → creator or a manager of the owning team; cross-team readers do
not receive owner-side fields (`source_ref`, `metadata`, sharing lists, embedding info, access history).

The same rule exists in three synchronised forms in `backend/app/security/access.py`:
`can_read()` (Python, authoritative), `sql_read_filter()` (SQL listing/keyword search) and
`build_read_filter()` (Qdrant filter). Tests exercise all three.

## 4. Auditability

Recorded events include: login, failed login, logout, refresh (and refresh-token reuse), invalid
tokens / API keys, memory create/read/search/update/archive/restore/delete/share/unshare/verify/feedback,
duplicate & conflict detection, conflict resolution, agent/team/user/project creation and updates,
API-key creation/revocation, role changes, role-permission changes, agent task runs, re-indexing and
every permission denial. Each record stores timestamp, actor, actor type, actor team, `on_behalf_of`,
action, resource type/id, status (`success`/`allowed`/`denied`/`failure`), reason, IP, user agent,
request id and JSON metadata. Example:

```
Support-Agent  MEMORY_READ  memory #a47088a7  DENIED  reason: Unauthorized team
```

## 5. Rate limiting

Sliding-window limits (per minute, configurable): `RATE_LIMIT_AUTH_PER_MINUTE` (per IP),
`RATE_LIMIT_SEARCH_PER_MINUTE` and `RATE_LIMIT_WRITE_PER_MINUTE` (per principal). Exceeding a limit
returns `429` with `Retry-After`; the SDK honours it automatically. `X-Forwarded-For` is only trusted
when `TRUST_PROXY_HEADERS=true`.

## 6. Error handling

All errors share one JSON shape and never include stack traces or internal messages:

```json
{"error": {"code": "forbidden", "message": "Access denied: Unauthorized team", "request_id": "3f2c…"}}
```

| Status | Code | Typical cause |
|---|---|---|
| 401 | unauthorized | missing/invalid/expired token or API key |
| 403 | forbidden | missing permission, visibility rule, escalation attempt |
| 404 | not_found | unknown id, expired memory without `include_expired` |
| 409 | conflict | duplicate rejected, version mismatch, name taken, private share |
| 422 | validation_error | schema or policy violation (`details` lists fields) |
| 429 | rate_limited | rate limit exceeded |
| 500 | internal_error | unexpected error (logged with request id) |
| 503 | service_unavailable | vector DB / embedding model unavailable |

## 7. Hardening checklist for production

* set `ENVIRONMENT=production`, `COOKIE_SECURE=true`, serve over TLS (terminate at nginx or a load balancer)
* use `https://` for Qdrant and TLS for PostgreSQL; rotate `JWT_SECRET`, database and Qdrant keys
* move the rate limiter to Redis when running more than one backend replica
* restrict `/metrics` and `/docs` at the proxy if they must not be public
* keep `ALLOW_PUBLIC_REGISTRATION=false`
