#!/usr/bin/env python3
"""Create a local `.env` file from `.env.example` with strong random secrets.

Usage:
    python scripts/generate_env.py            # refuses to overwrite an existing .env
    python scripts/generate_env.py --force    # regenerate secrets

Only the Python standard library is used, so this works before any
dependency is installed. The generated secrets are printed once so the
operator can log in to the dashboard; they are never committed (``.env`` is
git-ignored).
"""
from __future__ import annotations

import argparse
import secrets
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="overwrite an existing .env")
    args = parser.parse_args()

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
    print(f"Wrote {TARGET}")
    print()
    print("Dashboard login (keep these private):")
    print("  username: admin")
    print(f"  password: {values['ADMIN_PASSWORD']}")
    print(f"  demo users password: {values['DEMO_USER_PASSWORD']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
