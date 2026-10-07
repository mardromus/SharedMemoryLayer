"""Importers, LLM tool adapters and the CLI (transport-level, no server needed)."""
from __future__ import annotations

import json

import httpx

from shared_memory import MemoryClient, importers
from shared_memory.cli import build_parser, cmd_mcp_config
from shared_memory.tools import MemoryToolkit

RUNBOOK = """---
owner: sre
---
# Payments runbook

Intro paragraph that is long enough to be kept as its own memory section.

## Gateway timeouts

When the gateway times out, check the upstream status page and fail over to the secondary provider.

## Duplicate charges

Use idempotency keys on every retried charge so customers are never billed twice.

### Tiny

short
"""


def test_markdown_split_by_heading():
    items = importers.from_markdown(RUNBOOK, "payments.md", tags=["Runbook", "payments"], visibility="team")
    titles = [i["title"] for i in items]
    assert titles == ["Payments runbook", "Payments runbook — Gateway timeouts", "Payments runbook — Duplicate charges"]
    assert all(i["tags"] == ["runbook", "payments"] and i["visibility"] == "team" for i in items)
    assert items[1]["source_ref"] == "payments.md#gateway-timeouts"
    assert "owner: sre" not in items[0]["content"]  # front matter stripped
    assert "short" in items[2]["content"]  # deeper headings stay inside their section


def test_markdown_single_file_and_long_content_chunking():
    body = "# Big doc\n\n" + "\n\n".join(f"Paragraph {i} " + "x" * 900 for i in range(40))
    items = importers.from_markdown(body, "big.md", split="file")
    assert len(items) > 1 and all(len(i["content"]) <= importers.MAX_CONTENT for i in items)
    assert items[0]["title"].startswith("Big doc (part 1")


def test_json_and_csv_records():
    js = importers.from_json(json.dumps({"memories": [{"title": "A decision", "content": "We chose X", "tags": "db; infra"}, {"title": "no content"}]}))
    assert js == [{"title": "A decision", "content": "We chose X", "tags": ["db", "infra"]}]
    csv_items = importers.from_csv("title,content,memory_type,confidence\nFAQ refund,Refunds take 5 days,documentation,0.9\n", visibility="organization")
    assert csv_items[0]["confidence"] == 0.9 and csv_items[0]["visibility"] == "organization"


def test_tool_schemas_and_dispatch():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path == "/api/memories/search":
            assert body["query"] == "expired tokens" and body["top_k"] == 2
            return httpx.Response(200, json={"query": "expired tokens", "results": [{"memory_id": "m1", "title": "JWT", "content": "c", "team": "Dev", "memory_type": "learning", "similarity": 0.9, "version": 2}]})
        return httpx.Response(403, json={"error": {"message": "Access denied: Unauthorized team"}})

    toolkit = MemoryToolkit(MemoryClient("http://t", api_key="k", http_client=httpx.Client(base_url="http://t", transport=httpx.MockTransport(handler))))
    assert {t["function"]["name"] for t in toolkit.openai_tools()} == {"search_memory", "remember", "get_memory"}
    assert all("input_schema" in t for t in toolkit.anthropic_tools())
    out = json.loads(toolkit.dispatch("search_memory", '{"query": "expired tokens", "top_k": 2}'))
    assert out["results"][0]["memory_id"] == "m1"
    denied = json.loads(toolkit.dispatch("remember", {"title": "abc", "content": "x"}))
    assert denied["status"] == 403 and "Unauthorized team" in denied["error"]
    assert "invalid arguments" in json.loads(toolkit.dispatch("search_memory", {}))["error"]


def test_bulk_import_batches(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        items = json.loads(request.content)["items"]
        calls.append(len(items))
        return httpx.Response(200, json={"created": len(items), "skipped": 0, "failed": 0,
                                         "results": [{"index": i, "status": "created"} for i in range(len(items))]})

    client = MemoryClient("http://t", api_key="k", http_client=httpx.Client(base_url="http://t", transport=httpx.MockTransport(handler)))
    res = client.remember_many([{"title": f"t{i}", "content": "c"} for i in range(250)])
    assert calls == [100, 100, 50] and res["created"] == 250 and res["results"][-1]["index"] == 249


def test_cli_parser_and_mcp_config(capsys):
    args = build_parser().parse_args(["--url", "http://sml.example", "mcp-config", "--client", "cursor"])
    cmd_mcp_config(args)
    cfg = json.loads(capsys.readouterr().out)
    assert cfg["mcpServers"]["shared-memory"]["url"] == "http://sml.example/mcp"
    assert cfg["mcpServers"]["shared-memory"]["headers"]["X-API-Key"] == "${SML_API_KEY}"
    args = build_parser().parse_args(["--url", "http://sml.example", "mcp-config"])
    cmd_mcp_config(args)
    assert capsys.readouterr().out.startswith("claude mcp add --transport http shared-memory http://sml.example/mcp")
