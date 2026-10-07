// Unit tests with a mocked fetch, plus an optional live test (set SML_URL and SML_API_KEY).
import assert from "node:assert/strict";
import { test } from "node:test";
import { SharedMemoryClient, SharedMemoryError } from "../dist/index.js";

function mockFetch(handler) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init, body: init.body ? JSON.parse(init.body) : undefined });
    const { status = 200, json, headers = {} } = handler(url, init, calls.length);
    return new Response(json === undefined ? null : JSON.stringify(json), { status, headers: { "content-type": "application/json", ...headers } });
  };
  fn.calls = calls;
  return fn;
}

test("search sends the API key and snake_case body", async () => {
  const fetch = mockFetch(() => ({ json: { query: "q", mode: "hybrid", total: 1, took_ms: 3, results: [{ memory_id: "m1", title: "JWT", similarity: 0.9 }] } }));
  const client = new SharedMemoryClient({ baseUrl: "http://sml.test/", apiKey: "sml_k", fetch });
  const res = await client.search("expired tokens", { topK: 3, mode: "hybrid", memoryTypes: ["learning"] });
  assert.equal(res.results[0].memory_id, "m1");
  const call = fetch.calls[0];
  assert.equal(call.url, "http://sml.test/api/memories/search");
  assert.equal(call.init.headers["X-API-Key"], "sml_k");
  assert.deepEqual(call.body, { query: "expired tokens", top_k: 3, mode: "hybrid", memory_types: ["learning"] });
});

test("errors carry status, code and request id", async () => {
  const fetch = mockFetch(() => ({ status: 403, json: { error: { code: "forbidden", message: "Access denied: Unauthorized team", request_id: "r1" } } }));
  const client = new SharedMemoryClient({ baseUrl: "http://sml.test", apiKey: "k", fetch });
  await assert.rejects(client.get("m1"), (err) => err instanceof SharedMemoryError && err.isPermissionDenied && err.requestId === "r1" && err.message.includes("Unauthorized team"));
});

test("429 is retried honouring Retry-After", async () => {
  const fetch = mockFetch((_u, _i, n) => (n < 2 ? { status: 429, json: { error: { message: "slow down" } }, headers: { "Retry-After": "0" } } : { json: { id: "a1", kind: "agent" } }));
  const client = new SharedMemoryClient({ baseUrl: "http://sml.test", apiKey: "k", fetch });
  assert.equal((await client.whoami()).kind, "agent");
  assert.equal(fetch.calls.length, 2);
});

test("rememberMany batches by 100", async () => {
  const fetch = mockFetch((_u, init) => {
    const n = JSON.parse(init.body).items.length;
    return { json: { created: n, skipped: 0, failed: 0, results: Array.from({ length: n }, (_, i) => ({ index: i, status: "created" })) } };
  });
  const client = new SharedMemoryClient({ baseUrl: "http://sml.test", apiKey: "k", fetch });
  const res = await client.rememberMany(Array.from({ length: 230 }, (_, i) => ({ title: `t${i}`, content: "c" })));
  assert.deepEqual(fetch.calls.map((c) => c.body.items.length), [100, 100, 30]);
  assert.equal(res.created, 230);
  assert.equal(res.results.at(-1).index, 229);
});

const live = process.env.SML_URL && process.env.SML_API_KEY;
test("live: remember, search, version and permission check", { skip: !live && "set SML_URL and SML_API_KEY" }, async () => {
  const client = new SharedMemoryClient({ baseUrl: process.env.SML_URL, apiKey: process.env.SML_API_KEY });
  const me = await client.whoami();
  assert.equal(me.kind, "agent");
  const marker = `TS-SDK-${Date.now()}`;
  const created = await client.remember({ title: `${marker} cache decision`, content: `${marker}: product cache TTL is 5 minutes.`, memoryType: "decision", tags: ["typescript-sdk"] });
  assert.equal(created.memory.version, 1);
  const found = await client.search(marker, { topK: 3 });
  assert.equal(found.results[0].memory_id, created.memory.id);
  assert.notEqual(found.results[0].match, "semantic"); // exact identifier caught by hybrid search
  const updated = await client.update(created.memory.id, { content: `${marker}: product cache TTL is 2 minutes.` }, { changeDescription: "shorter TTL" });
  assert.equal(updated.memory.version, 2);
  assert.deepEqual((await client.versions(created.memory.id)).map((v) => v.version_number), [2, 1]);
});
