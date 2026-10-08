#!/usr/bin/env python3
"""Create a local `.env` file from `.env.example` with strong random secrets.

Usage:
    python scripts/generate_env.py            # refuses to overwrite an existing .env
    python scripts/generate_env.py --force    # regenerate secrets
    python3 scripts/generate_env.py --production --domain memory.example.com
                                              # server deployment (docker-compose.prod.yml)

Only the Python standard library is used, so this works before any
dependency is installed. The generated secrets are printed once so the
operator can log in to the dashboard; they are never committed (``.env`` is
git-ignored).
"""
from __future__ import annotations

import argparse
import re
import secrets
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"
HOSTNAME = re.compile(r"^(localhost|(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63})$")


def _password(length: int = 20) -> str:
    """Password satisfying the backend policy (upper, lower, digit, symbol)."""
    alphabet = string.ascii_letters + string.digits
    while True:
        core = "".join(secrets.choice(alphabet) for _ in range(length - 2))
        candidate = core + secrets.choice("!@#%^*-_") + secrets.choice(string.digits)
        if (
            any(c.islower() for c in candidate)
            and any(c.isupper() for c in candidate)
            and any(c.isdigit() for c in candidate)
        ):
            return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="overwrite an existing .env")
    parser.add_argument("--production", action="store_true",
                        help="settings for docker-compose.prod.yml (HTTPS, secure cookies, proxy-aware rate limits)")
    parser.add_argument("--domain", help="public host name of the server, e.g. memory.example.com or 203-0-113-10.sslip.io")
    parser.add_argument("--no-demo-data", action="store_true", help="do not seed the demo teams, agents and memories")
    args = parser.parse_args()

    domain = (args.domain or "").strip().lower()
    if args.production:
        if not domain:
            parser.error("--production needs --domain (use <server-ip-with-dashes>.sslip.io without a domain name)")
        if not HOSTNAME.match(domain):
            parser.error(f"--domain must be a bare host name such as memory.example.com, got {args.domain!r}")
    elif domain:
        parser.error("--domain is only used with --production")

    if TARGET.exists() and not args.force:
        print(f"{TARGET} already exists. Use --force to regenerate it.")
        return 1

    values = {
        # URL-safe (hex) so it can be embedded in DATABASE_URL without escaping
        "POSTGRES_PASSWORD": secrets.token_hex(16),
        "QDRANT_API_KEY": secrets.token_hex(24),
        "JWT_SECRET": secrets.token_urlsafe(48),
        "ADMIN_PASSWORD": _password(),
        "DEMO_USER_PASSWORD": _password(),
    }
    if args.production:
        values.update({
            "ENVIRONMENT": "production",
            "DOMAIN": domain,
            "CORS_ORIGINS": f"https://{domain}",
            "COOKIE_SECURE": "true",
            "TRUST_PROXY_HEADERS": "true",
        })
    if args.no_demo_data:
        values["SEED_DEMO_DATA"] = "false"

    lines = []
    for raw in EXAMPLE.read_text(encoding="utf-8").splitlines():
        key, sep, value = raw.partition("=")
        key = key.strip()
        if sep and not raw.lstrip().startswith("#"):
            if key in values:
                raw = f"{key}={values[key]}"
            elif "${POSTGRES_PASSWORD}" in value:
                raw = f"{key}={value.replace('${POSTGRES_PASSWORD}', values['POSTGRES_PASSWORD'])}"
        lines.append(raw)

    TARGET.write_text("\n".join(lines) + "\n", encoding="utf-8")
    TARGET.chmod(0o600)
    print(f"Wrote {TARGET}")
    if args.production:
        print(f"Dashboard URL: https://{domain}")
    print()
    print("Dashboard login (keep these private; they are also stored in .env):")
    print("  username: admin")
    print(f"  password: {values['ADMIN_PASSWORD']}")
    print(f"  demo users password: {values['DEMO_USER_PASSWORD']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
