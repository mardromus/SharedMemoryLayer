#!/usr/bin/env bash
# Deploy or update the production stack on a Linux server with Docker (docs/deployment.md).
#
#   scripts/deploy.sh memory.example.com [--no-demo-data]
#       first deployment: writes .env with fresh secrets, builds and starts the stack
#   scripts/deploy.sh
#       update (after `git pull`): rebuilds and restarts with the existing .env
#
# No domain name? Use <server-ip-with-dashes>.sslip.io, e.g. 203-0-113-10.sslip.io
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose -f docker-compose.prod.yml "$@"; }
env_value() { grep -E "^$1=" .env | tail -n 1 | cut -d= -f2-; }

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker with the Compose plugin is required. On Ubuntu/Debian:" >&2
  echo "  curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker \$USER   # then log in again" >&2
  exit 1
fi

if [ ! -f .env ]; then
  if [ $# -lt 1 ]; then
    echo "usage: scripts/deploy.sh <domain> [--no-demo-data]   (no domain? use <server-ip-with-dashes>.sslip.io)" >&2
    exit 1
  fi
  python3 scripts/generate_env.py --production --domain "$@"
elif [ $# -ge 1 ] && [ "$1" != "$(env_value DOMAIN)" ]; then
  echo ".env already exists with DOMAIN=$(env_value DOMAIN). To change the domain, edit DOMAIN in .env" >&2
  echo "(or regenerate it: python3 scripts/generate_env.py --production --domain <host> --force, which also resets all secrets)." >&2
  exit 1
fi

if [ "$(env_value ENVIRONMENT)" != "production" ] || [ -z "$(env_value DOMAIN)" ]; then
  echo ".env is not a production configuration. Create one with:" >&2
  echo "  python3 scripts/generate_env.py --production --domain <host> --force" >&2
  exit 1
fi

DOMAIN=$(env_value DOMAIN)
HTTPS_PORT=$(env_value HTTPS_PORT)
URL="https://$DOMAIN"
if [ -n "$HTTPS_PORT" ] && [ "$HTTPS_PORT" != "443" ]; then URL="$URL:$HTTPS_PORT"; fi

echo "Building and starting the stack for $URL (the first build downloads the embedding model) ..."
compose up -d --build --wait --remove-orphans
compose ps

if command -v curl >/dev/null 2>&1; then
  # DOMAIN=localhost gets a certificate from Caddy's local CA, which curl does not trust.
  insecure=""; [ "$DOMAIN" = "localhost" ] && insecure="-k"
  echo "Waiting for HTTPS (certificate issuance can take a minute) ..."
  ok=""
  for _ in $(seq 1 45); do
    if curl -fsS $insecure "$URL/health" >/dev/null; then ok=1; break; fi
    sleep 4
  done
  if [ -z "$ok" ]; then
    echo "$URL/health did not answer. Check that $DOMAIN resolves to this server and ports 80/443 are open:" >&2
    echo "  docker compose -f docker-compose.prod.yml logs caddy" >&2
    exit 1
  fi
fi

echo
echo "Live at $URL"
echo "  dashboard login: admin / ADMIN_PASSWORD in .env"
echo "  API docs: $URL/docs    MCP endpoint: $URL/mcp"
