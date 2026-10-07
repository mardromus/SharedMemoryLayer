#!/usr/bin/env python3
"""Final acceptance scenario, executed against a running deployment.

 1. services up (health)          9. Security agent tries a confidential Development memory
 2. admin login                  10. access is denied (403)
 3. create Development/QA/Security teams   11. the denial is in the audit log
 4. create Dev/QA/Security agents 12. the original memory is updated
 5. Dev agent stores the JWT memory        13. a new version exists
 6. embedding generated + stored 14. QA searches again
 7. QA agent searches            15. the latest authorised version is returned
 8. QA retrieves the memory      16. dashboard statistics reflect the operations

Usage:
    docker compose exec backend python /opt/scripts/acceptance_test.py
    python scripts/acceptance_test.py --base-url http://localhost:8000 --env-file .env   (host, needs `pip install ./sdk`)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from shared_memory import MemoryClient, PermissionDeniedError

PASSED: list[str] = []


def step(n: int, text: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {n:>2}. {text}" + (f"  — {detail}" if detail else ""), flush=True)
    if not ok:
        raise SystemExit(f"Acceptance scenario failed at step {n}: {text}")
    PASSED.append(text)


def load_env(path: str | None) -> None:
    if path and Path(path).exists():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("SML_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--env-file")
    args = parser.parse_args()
    load_env(args.env_file)
    suffix = datetime.now().strftime("%H%M%S")

    probe = MemoryClient(args.base_url, access_token="none")
    health = probe.request("GET", "/health")
    comps = health["components"]
    step(1, "All services are up", health["status"] == "ok",
         f"postgres={comps['database']} qdrant={comps['vector_db']} embeddings={comps['embedding_model']['model']}")

    admin = MemoryClient(args.base_url, username=os.environ["ADMIN_USERNAME"], password=os.environ["ADMIN_PASSWORD"])
    me = admin.whoami()
    step(2, "Admin logs in", "ADMIN" in me.roles, f"user={me.name}")
    before = admin.request("GET", "/api/dashboard/stats")["counts"]

    teams = {}
    for key, label in (("dev", "Development"), ("qa", "QA"), ("sec", "Security")):
        teams[key] = admin.request("POST", "/api/teams", json={"name": f"{label} AT-{suffix}"})
    step(3, "Development, QA and Security teams created", len(teams) == 3, ", ".join(t["name"] for t in teams.values()))

    agents, clients = {}, {}
    for key, label in (("dev", "Dev"), ("qa", "QA"), ("sec", "Security")):
        agents[key] = admin.request("POST", "/api/agents", json={"name": f"{label}-Agent-AT-{suffix}", "team_id": teams[key]["id"]})
        clients[key] = MemoryClient(args.base_url, api_key=agents[key]["api_key"])
    step(4, "Dev, QA and Security agents registered with API keys", all(a["api_key"] for a in agents.values()),
         ", ".join(a["name"] for a in agents.values()))

    dev, qa, sec = clients["dev"], clients["qa"], clients["sec"]
    created = dev.remember(
        "JWT authentication fails when refresh tokens expire",
        "JWT authentication fails when refresh tokens expire.",
        memory_type="learning", tags=["authentication", "jwt"], visibility="selected_teams",
        allowed_team_ids=[teams["qa"]["id"]], source_ref=f"acceptance-{suffix}",
    )
    memory = created.memory
    confidential = dev.remember(
        "Confidential: internal signing key rotation schedule",
        "Signing keys for the Development team's services are rotated on the first Monday of each quarter.",
        memory_type="context", visibility="team", sensitivity="confidential",
    ).memory
    step(5, "Dev agent stores the JWT memory (shared with QA only)", memory.version == 1, f"memory {memory.id}")

    consistency = admin.request("GET", "/api/admin/consistency")
    step(6, "Embedding generated and stored in Qdrant", memory.embedding_model is not None and memory.id not in consistency["missing_vectors"],
         f"model={memory.embedding_model}, vectors={consistency['vector_points']}")

    query = "authentication failures caused by expired tokens"
    results = qa.search(query, top_k=5)
    step(7, "QA agent runs a semantic search", results.mode in ("hybrid", "semantic"), f"'{query}' -> {len(results)} results in {results.took_ms} ms (mode={results.mode})")
    hit = next((r for r in results if r.memory_id == memory.id), None)
    step(8, "QA agent retrieves the Development memory", hit is not None,
         f"rank {[r.memory_id for r in results].index(memory.id) + 1 if hit else '-'}, similarity {hit.similarity if hit else '-'}")

    denied = False
    try:
        sec.get(confidential.id)
    except PermissionDeniedError as exc:
        denied, reason = True, exc.message
    step(9, "Security agent attempts to open a confidential Development memory", True, f"memory {confidential.id}")
    step(10, "Access is denied", denied, reason if denied else "ACCESS WAS GRANTED")

    time.sleep(0.5)
    events = admin.request("GET", "/api/audit-logs", params={"status": "denied", "resource_id": confidential.id})["items"]
    ev = next((e for e in events if e["actor_name"] == agents["sec"]["name"]), None)
    step(11, "The denied attempt appears in the audit log", ev is not None,
         f"{ev['actor_name']} {ev['action']} {ev['status'].upper()} reason='{ev['reason']}'" if ev else "")

    updated = dev.update(memory.id, content="JWT authentication fails when refresh tokens expire. Fix: return 401 token_expired and re-authenticate.",
                         change_description="Added the fix")
    step(12, "Original memory updated", updated.memory.version == 2)
    versions = dev.versions(memory.id)
    step(13, "A new version was created", [v.version_number for v in versions] == [2, 1],
         f"v1='…{versions[1].content[-35:]}' -> v2='…{versions[0].content[-35:]}'")

    again = qa.search(query, top_k=5)
    hit2 = next((r for r in again if r.memory_id == memory.id), None)
    step(14, "QA agent searches again", hit2 is not None)
    step(15, "Latest authorised version is returned", hit2 is not None and hit2.version == 2 and "401" in hit2.content, f"version {hit2.version if hit2 else '-'}")

    after = admin.request("GET", "/api/dashboard/stats")["counts"]
    checks = {
        "memories_today": after["memories_today"] >= before["memories_today"] + 2,
        "searches_today": after["searches_today"] >= before["searches_today"] + 2,
        "access_denials_today": after["access_denials_today"] >= before["access_denials_today"] + 1,
        "agents": after["agents"] >= before["agents"] + 3,
        "teams": after["teams"] >= before["teams"] + 3,
    }
    step(16, "Dashboard statistics reflect the operations", all(checks.values()),
         ", ".join(f"{k}: {before[k]}->{after[k]}" for k in checks))

    print(f"\nAcceptance scenario passed ({len(PASSED)} checks). Created entities use suffix AT-{suffix}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
