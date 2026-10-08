# Deploying to a server

This guide puts the Shared Memory Layer on the internet: one Linux server, HTTPS with an automatic
certificate, the dashboard at `https://<your-domain>`, and the API and MCP endpoint on the same address
for agents anywhere. Everything runs from `docker-compose.prod.yml`; the only tools needed on the server
are Docker, Git and Python 3.

```
Internet ──443──▶ Caddy (TLS, Let's Encrypt) ──▶ nginx (dashboard; /api /mcp /docs /health) ──▶ FastAPI
                                                                                                │
                                                          internal Docker network only ─────────┴──▶ PostgreSQL, Qdrant
```

| | Development (`docker-compose.yml`) | Production (`docker-compose.prod.yml`) |
|---|---|---|
| Address | `http://localhost:3000`, API also on `:8000` | `https://<domain>` only — ports 80 and 443 |
| TLS | none | Caddy, automatic Let's Encrypt certificate and renewal, HTTP → HTTPS redirect, HSTS |
| Cookies | `COOKIE_SECURE=false` | `ENVIRONMENT=production`, secure cookies (enforced by the backend) |
| Rate limits | per connection | per real client IP, taken from the proxy chain; a client cannot forge it |
| Database, vector DB, API | ports published for development | not reachable from outside |
| Logs | unbounded | rotated (5 × 10 MB per container) |
| Test database, evaluation volume | yes | no |

## 1. A server

Any Linux virtual machine with Docker works — x86-64 or ARM64.

* **Size:** 2 vCPU and **4 GB RAM** recommended (2 GB is the minimum; the embedding model and the image
  build need the memory), 15 GB disk.
* **Operating system:** Ubuntu 24.04 or 22.04 LTS (any distribution with Docker works).
* **Firewall / security group:** allow inbound TCP **22** (SSH), **80** and **443**. Nothing else.
* **Where:** any cloud VM — e.g. DigitalOcean, Azure, AWS EC2, Google Cloud or Oracle Cloud. Students can get
  free cloud credit through the GitHub Student Developer Pack or Azure for Students; Oracle Cloud's Always
  Free tier includes ARM VMs large enough for this stack.

## 2. A domain name

The certificate needs a name that resolves to the server's public IP:

* **Own domain:** create a DNS **A record**, e.g. `memory.example.com → 203.0.113.10`.
* **No domain:** use the free wildcard DNS of [sslip.io](https://sslip.io): the name
  `203-0-113-10.sslip.io` resolves to `203.0.113.10`. Replace the dashes-separated IP with your server's.

## 3. Install Docker (once)

```bash
ssh ubuntu@203.0.113.10
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
exit                      # log in again so the group change applies
```

## 4. Deploy

```bash
git clone https://github.com/mardromus/SharedMemoryLayer.git
cd SharedMemoryLayer
scripts/deploy.sh memory.example.com            # or 203-0-113-10.sslip.io
```

`deploy.sh` creates `.env` with fresh random secrets (it prints the admin password once; it is also in
`.env`), builds the images, starts the stack, waits until every container is healthy and checks
`https://<domain>/health` over the real certificate. The first run takes a few minutes because it builds
the images and downloads the embedding model into the backend image.

Open `https://<domain>` and log in as **`admin`** with `ADMIN_PASSWORD` from `.env`
(`grep ADMIN_PASSWORD .env`). The demo teams, agents and memories are seeded on first start; for a real
installation without them deploy with `scripts/deploy.sh <domain> --no-demo-data`.

## 5. Connect agents

The dashboard's **Connect** page shows ready-to-copy commands with the public address, for example
for Claude Code:

```bash
claude mcp add --transport http shared-memory https://memory.example.com/mcp \
  --header "X-API-Key: <AGENT_API_KEY>"
```

Issue the agent's key on the Connect page (or **Agents → New key**). The Python and TypeScript SDKs and the
REST API use the same base URL (`https://memory.example.com`); the API reference is at `/docs`.

## 6. Operating it

All commands run in the repository directory on the server.

| Task | Command |
|---|---|
| Update to the latest code | `git pull && scripts/deploy.sh` |
| Status | `docker compose -f docker-compose.prod.yml ps` |
| Logs | `docker compose -f docker-compose.prod.yml logs -f backend` (or `caddy`, `frontend`, `postgres`) |
| Back up the database | `scripts/backup.sh` → `backups/shared_memory-<UTC time>.dump` (all dumps are kept; `KEEP=14 scripts/backup.sh` keeps only the 14 newest) |
| Daily backup | `crontab -e` → `0 3 * * * cd $HOME/SharedMemoryLayer && KEEP=14 scripts/backup.sh` |
| Stop / start | `docker compose -f docker-compose.prod.yml stop` / `scripts/deploy.sh` |
| Acceptance check against the live stack | `docker compose -f docker-compose.prod.yml exec backend python /opt/scripts/acceptance_test.py` (creates its own test teams, agents and memories) |

**Backups and restore.** PostgreSQL holds everything that matters — memories, versions, users, agents, API-key
hashes and the audit log; the Qdrant index is derived from it. Copy the dumps off the server (e.g.
`scp`) and keep `.env` somewhere private as well. To restore:

```bash
docker compose -f docker-compose.prod.yml exec -T postgres \
  sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < backups/shared_memory-<time>.dump
docker compose -f docker-compose.prod.yml restart backend
```

then rebuild the vector index from the restored data: **Overview → Rebuild vector index** on the dashboard,
or `POST /api/admin/reindex?recreate=true` as an administrator.

**LLM answers (optional).** The agent reasoning layer runs without an LLM (extractive synthesis). To use a
hosted model set `LLM_PROVIDER=openai` or `anthropic` and the matching API key in `.env`, then run
`scripts/deploy.sh`.

## 7. Security checklist

* Only ports 22, 80 and 443 open; PostgreSQL, Qdrant and the API port are never published.
* `.env` stays on the server (`chmod 600`, git-ignored); `scripts/check_secrets.py` verifies nothing secret is
  committed.
* `ALLOW_PUBLIC_REGISTRATION=false` (default): accounts are created by an administrator.
* For real use, deploy with `--no-demo-data` — the demo accounts share one password.
* Keep the server updated (`sudo apt update && sudo apt upgrade`) and pull new releases with `git pull`.

## 8. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `deploy.sh` stops at "did not answer" | The certificate could not be issued: the domain does not resolve to this server yet, or ports 80/443 are blocked by a firewall / security group. `docker compose -f docker-compose.prod.yml logs caddy` shows the reason; fix it and re-run `scripts/deploy.sh`. |
| Build killed / container restarts | Too little memory — use a 4 GB server or add swap (`sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`). |
| Login works but the session ends on reload | The site is not opened over HTTPS on the configured `DOMAIN`; secure cookies are only sent over HTTPS. |
| Search returns nothing after a restore | Rebuild the vector index (see Backups and restore). |
| Need to change the domain | Edit `DOMAIN` in `.env`, then `scripts/deploy.sh` (CORS and the certificate follow it). |

Testing the production stack on a laptop: `python3 scripts/generate_env.py --production --domain localhost`
(in a separate clone, so the development `.env` is kept) and `scripts/deploy.sh`; Caddy then uses a
locally generated certificate that browsers warn about.
