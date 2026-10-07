"""``sml`` — command-line access to the Shared Memory Layer.

Credentials come from flags or the environment:
    SML_URL        (default http://localhost:8000)
    SML_API_KEY    agent API key   — or —   SML_USERNAME / SML_PASSWORD

Examples:
    sml whoami
    sml search "authentication failures caused by expired tokens" -k 3
    sml remember "Cache TTL decision" "Product cache TTL is 5 minutes" --type decision --tags cache,performance
    echo "long text" | sml remember "Runbook: failover" - --type documentation
    sml import ./docs --split heading --tags runbook --skip-duplicates
    sml mcp-config --client claude-code
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from shared_memory import importers
from shared_memory.client import MemoryClient
from shared_memory.errors import MemoryAPIError


def _client(args: argparse.Namespace) -> MemoryClient:
    url = args.url or os.environ.get("SML_URL", "http://localhost:8000")
    key = args.api_key or os.environ.get("SML_API_KEY")
    if key:
        return MemoryClient(url, api_key=key)
    user, pw = os.environ.get("SML_USERNAME"), os.environ.get("SML_PASSWORD")
    if user and pw:
        return MemoryClient(url, username=user, password=pw)
    raise SystemExit("error: set SML_API_KEY (or SML_USERNAME/SML_PASSWORD), or pass --api-key")


def _print(data: Any, as_json: bool, human) -> None:
    if as_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        human(data)


def cmd_whoami(args) -> None:
    me = _client(args).whoami()
    _print(me, args.json, lambda m: print(f"{m['name']} ({m['kind']}) · team {m.get('team_name') or '-'} · roles {', '.join(m['roles'])}"))


def cmd_search(args) -> None:
    res = _client(args).search(args.query, top_k=args.k, memory_types=args.type or None, mode=args.mode)

    def human(_):
        if not res:
            print("No authorised memory matched.")
        for i, r in enumerate(res, 1):
            print(f"{i}. [{r.similarity:.3f} {r.get('match', 'semantic')}] {r.title}  ({r.team}, {r.memory_type}, v{r.version})")
            print(f"   {r.content[:200]}{'…' if len(r.content) > 200 else ''}")
            print(f"   id {r.memory_id}")

    _print(res.raw, args.json, human)


def cmd_remember(args) -> None:
    content = sys.stdin.read() if args.content in (None, "-") else args.content
    if not content.strip():
        raise SystemExit("error: empty content")
    res = _client(args).remember(args.title, content, args.type, tags=[t for t in (args.tags or "").split(",") if t], visibility=args.visibility)
    _print(res, args.json, lambda r: print(f"stored {r.memory.id} (v{r.memory.version}, {r.memory.visibility})" + (f"\n⚠ {r.warnings.message}" if r.warnings.message else "")))


def cmd_get(args) -> None:
    m = _client(args).get(args.memory_id)
    _print(m, args.json, lambda m: print(f"{m.title}\n{'=' * len(m.title)}\n{m.content}\n\n{m.team['name']} · {m.memory_type} · v{m.version} · {', '.join(m.tags)}"))


def cmd_import(args) -> None:
    items: list[dict] = []
    opts = {"split": args.split, "level": args.level, "memory_type": args.type, "tags": [t for t in (args.tags or "").split(",") if t]}
    if args.visibility:
        opts["visibility"] = args.visibility
    for path in args.paths:
        items.extend(importers.load_path(path, **opts))
    if not items:
        raise SystemExit("error: nothing to import (supported: .md .markdown .txt .json .csv)")
    if args.dry_run:
        for it in items:
            print(f"- {it['title']}  ({len(it['content'])} chars, {it.get('memory_type', 'knowledge')})")
        print(f"{len(items)} memories would be imported (dry run)")
        return
    res = _client(args).remember_many(items, skip_duplicates=args.skip_duplicates)

    def human(r):
        for x in r["results"]:
            if x["status"] != "created":
                print(f"  {x['status']}: {x.get('title')} — {(x.get('error') or {}).get('message', '')}")
        print(f"created {r['created']} · skipped {r['skipped']} · failed {r['failed']}")

    _print(res, args.json, human)
    if res["failed"]:
        sys.exit(1)


def cmd_mcp_config(args) -> None:
    url = (args.url or os.environ.get("SML_URL", "http://localhost:8000")).rstrip("/") + "/mcp"
    key = "${SML_API_KEY}" if not args.show_key else (args.api_key or os.environ.get("SML_API_KEY", "<api-key>"))
    if args.client == "claude-code":
        print(f'claude mcp add --transport http shared-memory {url} --header "X-API-Key: {key}"')
    elif args.client == "claude-desktop":
        print(json.dumps({"mcpServers": {"shared-memory": {"command": "npx", "args": ["-y", "mcp-remote", url, "--header", f"X-API-Key:{key}"]}}}, indent=2))
    else:  # cursor / vscode / generic streamable-http clients
        print(json.dumps({"mcpServers": {"shared-memory": {"url": url, "headers": {"X-API-Key": key}}}}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sml", description="Shared Memory Layer command-line client")
    p.add_argument("--url", help="API base URL (env SML_URL)")
    p.add_argument("--api-key", help="agent API key (env SML_API_KEY)")
    p.add_argument("--json", action="store_true", help="print raw JSON")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("whoami", help="show the authenticated identity").set_defaults(fn=cmd_whoami)

    s = sub.add_parser("search", help="hybrid semantic + keyword search")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=5)
    s.add_argument("--type", action="append", help="filter by memory type (repeatable)")
    s.add_argument("--mode", choices=["hybrid", "semantic", "keyword"])
    s.set_defaults(fn=cmd_search)

    r = sub.add_parser("remember", help="store a memory ('-' reads content from stdin)")
    r.add_argument("title")
    r.add_argument("content", nargs="?")
    r.add_argument("--type", default="knowledge")
    r.add_argument("--tags", help="comma separated")
    r.add_argument("--visibility", choices=["private", "team", "selected_teams", "selected_agents", "organization"])
    r.set_defaults(fn=cmd_remember)

    g = sub.add_parser("get", help="show one memory")
    g.add_argument("memory_id")
    g.set_defaults(fn=cmd_get)

    i = sub.add_parser("import", help="bulk import Markdown / JSON / CSV files or folders")
    i.add_argument("paths", nargs="+")
    i.add_argument("--split", choices=["heading", "file"], default="heading")
    i.add_argument("--level", type=int, default=2, help="deepest heading level that starts a new memory")
    i.add_argument("--type", default="documentation")
    i.add_argument("--tags", help="comma separated tags added to every item")
    i.add_argument("--visibility", choices=["private", "team", "selected_teams", "selected_agents", "organization"])
    i.add_argument("--skip-duplicates", action="store_true", help="skip items that already exist (safe re-runs)")
    i.add_argument("--dry-run", action="store_true")
    i.set_defaults(fn=cmd_import)

    m = sub.add_parser("mcp-config", help="print MCP client configuration for this server")
    m.add_argument("--client", choices=["claude-code", "claude-desktop", "cursor", "vscode"], default="claude-code")
    m.add_argument("--show-key", action="store_true", help="inline the key instead of ${SML_API_KEY}")
    m.set_defaults(fn=cmd_mcp_config)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except MemoryAPIError as exc:
        raise SystemExit(f"error [{exc.status_code}]: {exc.message}") from None


if __name__ == "__main__":
    main()
