"""Real-world access paths: hybrid search, bulk import and the MCP endpoint."""
from __future__ import annotations

import socket
import threading
import time
import uuid

import anyio
import pytest
import uvicorn

from tests.conftest import unique


def _project(client, factory, team) -> dict:
    r = client.post("/api/projects", headers=factory.admin, json={"name": unique("rw"), "team_id": team["id"]})
    assert r.status_code == 201
    return r.json()


# ------------------------------------------------------------------ hybrid search
def test_hybrid_search_finds_exact_identifiers(client, factory, org):
    dev, qa = org["agents"]["dev"], org["agents"]["qa"]
    project = _project(client, factory, org["teams"]["dev"])
    code = f"ERR-{uuid.uuid4().int % 90000 + 10000}"
    target = factory.memory(dev, title="Payment gateway rejects unsupported currencies",
                            content=f"The gateway answers {code} when the merchant currency is not enabled; enable the currency in the merchant profile.",
                            visibility="organization", project_id=project["id"])["memory"]
    for text in ("Payment gateway times out during peak sales", "Refunds to expired cards fail at the gateway",
                 "Gateway webhook signature validation fails after key rotation", "Payment retries use exponential backoff"):
        factory.memory(dev, title=text, content=f"{text}. Investigated by the payments squad.", visibility="organization", project_id=project["id"])

    hybrid = factory.search(qa, code, project_ids=[project["id"]], similarity_threshold=0.0)
    assert hybrid["mode"] == "hybrid"
    assert hybrid["results"][0]["memory_id"] == target["id"]
    assert hybrid["results"][0]["match"] in ("keyword", "semantic+keyword")
    assert hybrid["results"][0]["similarity"] > 0  # keyword-only hits still carry a real cosine similarity

    keyword = factory.search(qa, code, project_ids=[project["id"]], mode="keyword")
    assert [r["memory_id"] for r in keyword["results"]] == [target["id"]]

    semantic = factory.search(qa, "why is the merchant currency rejected by the payment gateway", project_ids=[project["id"]], mode="semantic", similarity_threshold=0.0)
    assert semantic["mode"] == "semantic" and all(r["match"] == "semantic" for r in semantic["results"])


def test_keyword_search_respects_permissions(client, factory, org):
    sec, support = org["agents"]["sec"], org["agents"]["support"]
    token = f"CVE-2026-{uuid.uuid4().int % 90000 + 10000}"
    secret = factory.memory(sec, title=f"{token} internal exploit notes", content=f"{token} is exploitable through the export service.", visibility="team")["memory"]
    for mode in ("keyword", "hybrid"):
        outsider = factory.search(support, token, mode=mode, similarity_threshold=0.0, top_k=50)
        assert secret["id"] not in {r["memory_id"] for r in outsider["results"]}
        owner = factory.search(sec, token, mode=mode, similarity_threshold=0.0, top_k=50)
        assert secret["id"] in {r["memory_id"] for r in owner["results"]}


# ------------------------------------------------------------------ bulk import
def test_bulk_import_with_per_item_errors_and_idempotent_rerun(client, factory, org):
    dev = org["agents"]["dev"]
    marker = unique("runbook")
    items = [
        {"title": f"{marker}: restart the ingestion worker", "content": "Drain the queue, then restart the ingestion worker pods one by one.", "memory_type": "documentation", "tags": ["runbook"]},
        {"title": f"{marker}: rotate the CDN certificate", "content": "Upload the new certificate, then switch the listener; old certificate stays for 24h.", "memory_type": "documentation"},
        {"title": "x", "content": "invalid: title too short"},
        {"title": f"{marker}: bad type", "content": "valid text", "memory_type": "gossip"},
    ]
    r = client.post("/api/memories/bulk", headers=dev["headers"], json={"items": items})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["created"], body["failed"], body["skipped"]) == (2, 2, 0)
    assert [x["status"] for x in body["results"]] == ["created", "created", "error", "error"]
    assert body["results"][2]["error"]["code"] == "validation_error"

    again = client.post("/api/memories/bulk", headers=dev["headers"], json={"items": items[:2], "skip_duplicates": True}).json()
    assert (again["created"], again["skipped"]) == (0, 2)
    assert factory.audit(action="MEMORY_BULK_IMPORT", actor=dev["name"])


def test_bulk_import_requires_permission_and_limits(client, factory, org):
    reader = factory.user("READ_ONLY", org["teams"]["dev"])
    assert client.post("/api/memories/bulk", headers=reader["headers"], json={"items": [{"title": "abc", "content": "x"}]}).status_code == 403
    too_many = [{"title": f"item {i}", "content": "x"} for i in range(101)]
    assert client.post("/api/memories/bulk", headers=org["agents"]["dev"]["headers"], json={"items": too_many}).status_code == 422


# ------------------------------------------------------------------ MCP endpoint (real server + official MCP client)
@pytest.fixture(scope="module")
def live_server(infrastructure):
    from app.main import create_app

    app = create_app(run_startup=False)  # own instance: the MCP session manager runs once per app
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.1)
    assert server.started, "uvicorn did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


async def _mcp_session_run(url: str, api_key: str, fn):
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    async with create_mcp_http_client(headers={"X-API-Key": api_key}) as http:
        async with streamable_http_client(f"{url}/mcp", http_client=http) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await fn(session)


def _payload(result) -> dict:
    import json

    assert not result.is_error, result.content
    structured = getattr(result, "structured_content", None)
    if structured:
        return structured.get("result", structured)
    return json.loads(result.content[0].text)


def test_mcp_tools_end_to_end(live_server, factory, org):
    dev, qa, support = org["agents"]["dev"], org["agents"]["qa"], org["agents"]["support"]
    title = unique("MCP learning")

    async def as_dev(session):
        tools = {t.name for t in (await session.list_tools()).tools}
        assert {"search_memory", "remember", "get_memory", "update_memory", "share_memory", "memory_history", "list_teams", "whoami"} <= tools
        me = _payload(await session.call_tool("whoami", {}))
        assert me["name"] == dev["name"] and me["kind"] == "agent"
        created = _payload(await session.call_tool("remember", {
            "title": title, "content": "Feature flag evaluation is cached for 30 seconds per instance.",
            "memory_type": "learning", "visibility": "team", "share_with_teams": [org["teams"]["qa"]["name"]],
        }))
        updated = _payload(await session.call_tool("update_memory", {"memory_id": created["memory_id"], "content": "Feature flags are cached for 10 seconds per instance.", "change_description": "shorter TTL"}))
        assert updated["version"] == 2
        history = _payload(await session.call_tool("memory_history", {"memory_id": created["memory_id"]}))
        assert [v["version_number"] for v in history["versions"]] == [2, 1]
        return created["memory_id"]

    memory_id = anyio.run(_mcp_session_run, live_server, dev["api_key"], as_dev)

    async def as_qa(session):
        found = _payload(await session.call_tool("search_memory", {"query": "how long are feature flags cached", "top_k": 10}))
        hit = next(r for r in found["results"] if r["memory_id"] == memory_id)
        assert hit["version"] == 2 and hit["cross_team"] is True

    anyio.run(_mcp_session_run, live_server, qa["api_key"], as_qa)

    async def as_support(session):
        denied = await session.call_tool("get_memory", {"memory_id": memory_id})
        assert denied.is_error and "Access denied" in denied.content[0].text

    anyio.run(_mcp_session_run, live_server, support["api_key"], as_support)
    events = factory.audit(action="MEMORY_READ", status="denied", resource_id=memory_id)
    assert events and events[0]["actor_name"] == support["name"]


def test_mcp_rejects_missing_or_bad_credentials(live_server):
    import httpx

    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    assert httpx.post(f"{live_server}/mcp", json=body, headers=headers).status_code == 401
    assert httpx.post(f"{live_server}/mcp", json=body, headers={**headers, "X-API-Key": "sml_000000000000_nope"}).status_code == 401
