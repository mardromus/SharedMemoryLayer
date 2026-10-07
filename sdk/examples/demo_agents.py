#!/usr/bin/env python3
"""Multi-agent demonstration on top of the seeded demo organisation.

An administrator issues API keys for the four seeded agents, then:
  1. Support-Agent reports a new customer incident.
  2. Dev-Agent searches shared memory, finds the incident *and* earlier knowledge,
     stores the fix and shares it with QA.
  3. QA-Agent asks the memory-augmented runtime for test cases and gets the
     Development knowledge as context (cross-team reuse, no re-investigation).
  4. Support-Agent tries to open a confidential Security memory -> 403, audited.

Run inside the backend container (ADMIN_* come from .env):
    docker compose exec backend python /opt/sdk/examples/demo_agents.py
or from the host:
    pip install ./sdk && python sdk/examples/demo_agents.py --base-url http://localhost:8000 --env-file .env
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from shared_memory import MemoryClient, PermissionDeniedError


def banner(text: str) -> None:
    print(f"\n=== {text} " + "=" * max(0, 70 - len(text)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("SML_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--env-file")
    args = parser.parse_args()
    if args.env_file and Path(args.env_file).exists():
        for line in Path(args.env_file).read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    admin = MemoryClient(args.base_url, username=os.environ["ADMIN_USERNAME"], password=os.environ["ADMIN_PASSWORD"])
    agents = {a["name"]: a for a in admin.request("GET", "/api/agents")}
    missing = {"Dev-Agent", "QA-Agent", "Security-Agent", "Support-Agent"} - set(agents)
    if missing:
        raise SystemExit(f"Demo agents missing ({', '.join(sorted(missing))}); start the stack with SEED_DEMO_DATA=true")

    def client(name: str) -> MemoryClient:
        key = admin.request("POST", f"/api/agents/{agents[name]['id']}/api-keys", json={"name": "demo-script"})["api_key"]
        return MemoryClient(args.base_url, api_key=key)

    dev, qa, support = client("Dev-Agent"), client("QA-Agent"), client("Support-Agent")
    teams = {t["name"]: t["id"] for t in admin.request("GET", "/api/teams")}

    banner("1. Support-Agent records a customer incident")
    incident = support.remember(
        "Mobile users signed out after app update",
        "After the 4.2 mobile release several customers are signed out every few minutes and see 'session expired'.",
        memory_type="incident", tags=["customer-issue", "authentication", "mobile"], visibility="organization",
    )
    print("stored", incident.memory.id, "| warnings:", incident.warnings.message)

    banner("2. Dev-Agent investigates using shared memory")
    for r in dev.search("users are repeatedly signed out because their session expires", top_k=4):
        print(f"  {r.similarity:.3f}  [{r.team:<11}] {r.title}")
    fix = dev.remember(
        "Mobile 4.2 refresh-token rotation bug",
        "The 4.2 mobile client discarded the rotated refresh token and reused the old one, which the server revoked "
        "(refresh-token reuse detection). Fix: persist the new refresh token after every refresh.",
        memory_type="solution", tags=["authentication", "mobile", "refresh-token"], visibility="team",
        allowed_team_ids=[teams["QA"], teams["Support"]], parent_memory_id=incident.memory.id,
    )
    print("stored fix", fix.memory.id, "shared with QA + Support")

    banner("3. QA-Agent asks the runtime for test cases")
    out = qa.run_task("Which regression tests should cover refresh-token rotation on mobile?", top_k=4)
    print(f"provider={out.provider}; memories used from teams: {sorted({m.team for m in out.memories_used})}")
    print(out.answer)

    banner("4. Support-Agent tries a confidential Security memory")
    secret = next(m for m in admin.request("GET", "/api/memories", params={"q": "SSRF"})["items"])
    try:
        support.get(secret["id"])
        print("UNEXPECTED: access granted")
    except PermissionDeniedError as exc:
        print("ACCESS DENIED ->", exc.message, f"(request {exc.request_id}; recorded in the audit log)")


if __name__ == "__main__":
    main()
