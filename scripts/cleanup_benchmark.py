#!/usr/bin/env python3
"""Remove data left behind by an interrupted benchmark run (evaluation or LoCoMo).

Deletes every memory created by agents of teams whose name starts with the given
prefix (default: "LoCoMo " and "Eval "), then the agents, then deactivates the teams.

    docker compose exec backend python /opt/scripts/cleanup_benchmark.py [--prefix "LoCoMo "] [--dry-run]
"""
from __future__ import annotations

import argparse
import os

from shared_memory import MemoryAPIError, MemoryClient


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("SML_BASE_URL", "http://localhost:8000"))
    ap.add_argument("--prefix", action="append", help="team name prefix (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    prefixes = tuple(args.prefix or ["LoCoMo ", "Eval "])
    admin = MemoryClient(args.base_url, username=os.environ["ADMIN_USERNAME"], password=os.environ["ADMIN_PASSWORD"], timeout=300)
    teams = [t for t in admin.request("GET", "/api/teams", params={"include_inactive": "true"}) if t["name"].startswith(prefixes)]
    for team in teams:
        agents = admin.request("GET", "/api/agents", params={"team_id": team["id"]})
        deleted = 0
        for agent in agents:
            while True:
                page = admin.request("GET", "/api/memories", params={"agent_id": agent["id"], "page_size": 200, "status": "active", "include_expired": "true"})
                archived = admin.request("GET", "/api/memories", params={"agent_id": agent["id"], "page_size": 200, "status": "archived", "include_expired": "true"})
                items = page["items"] + archived["items"]
                if not items or args.dry_run:
                    deleted += len(items)
                    break
                for m in items:
                    try:
                        admin.request("DELETE", f"/api/memories/{m['id']}", params={"permanent": "true"})
                        deleted += 1
                    except MemoryAPIError:
                        pass
            if not args.dry_run:
                admin.request("DELETE", f"/api/agents/{agent['id']}")
        if not args.dry_run and team["is_active"]:
            admin.request("PATCH", f"/api/teams/{team['id']}", json={"is_active": False})
        print(f"{'would clean' if args.dry_run else 'cleaned'} team {team['name']}: {len(agents)} agents, {deleted} memories")


if __name__ == "__main__":
    main()
