#!/usr/bin/env python3
"""Verify that no secret is committed to the repository.

Checks
  1. `.env` is git-ignored and not tracked.
  2. None of the secret *values* from the local `.env` appears in any tracked /
     committable file.
  3. No file contains common credential patterns (private keys, cloud keys,
     OpenAI/Anthropic keys, generated agent API keys, JWTs).

Standard library only:  python scripts/check_secrets.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRET_KEYS = {"POSTGRES_PASSWORD", "QDRANT_API_KEY", "JWT_SECRET", "ADMIN_PASSWORD", "DEMO_USER_PASSWORD", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "OpenAI key": re.compile(r"sk-[A-Za-z0-9]{32,}"),
    "Anthropic key": re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}"),
    "agent API key": re.compile(r"sml_[0-9a-f]{12}_[A-Za-z0-9\-_]{30,}"),
    "JWT": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
}
SKIP_DIRS = {".git", "node_modules", "dist", "__pycache__", ".pytest_cache", "results"}


def candidate_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        return [ROOT / line for line in out.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        return [p for p in ROOT.rglob("*") if p.is_file() and not SKIP_DIRS & set(p.parts) and p.name != ".env"]


def main() -> int:
    problems: list[str] = []
    env = ROOT / ".env"
    try:
        ignored = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROOT).returncode == 0
        tracked = bool(subprocess.run(["git", "ls-files", ".env"], cwd=ROOT, capture_output=True, text=True).stdout.strip())
        if not ignored:
            problems.append(".env is not git-ignored")
        if tracked:
            problems.append(".env is tracked by git")
    except OSError:
        print("git not available - skipping ignore checks")

    secrets: dict[str, str] = {}
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() in SECRET_KEYS and len(value.strip()) >= 8:
                secrets[key.strip()] = value.strip()

    files = candidate_files()
    for path in files:
        if not path.is_file() or path.name == ".env" or path.suffix in {".png", ".jpg", ".ico", ".onnx"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(ROOT)
        for key, value in secrets.items():
            if value in text:
                problems.append(f"{rel}: contains the value of {key} from .env")
        for name, pattern in PATTERNS.items():
            if pattern.search(text):
                problems.append(f"{rel}: looks like it contains a {name}")

    print(f"Scanned {len(files)} committable files for {len(secrets)} local secret values and {len(PATTERNS)} credential patterns.")
    if problems:
        print("FAIL:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("PASS: no secrets found; .env is git-ignored and untracked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
