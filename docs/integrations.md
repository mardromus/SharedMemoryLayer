# Connecting agents and tools

Every way in uses the same identity model: an **agent API key** (or a user JWT). The agent only ever
reads what its team may see, and every call is audited. The dashboard page **Connect an agent**
generates a key and fills it into all of the snippets below.

In the examples `BASE` is the public URL of the deployment — `http://localhost:3000` through nginx
(or `http://localhost:8000` straight to the API).

## 1. MCP — Claude Code, Cursor, VS Code, Claude Desktop, any MCP client

The backend serves the [Model Context Protocol](https://modelcontextprotocol.io) over Streamable
HTTP at **`BASE/mcp`**, authenticated with `X-API-Key` (or `Authorization: Bearer`). Tools:

| Tool | What it does |
|---|---|
| `search_memory` | Hybrid semantic + keyword search over memories the agent may read |
| `remember` | Store a decision / learning / incident / solution (duplicate & conflict warnings returned) |
| `get_memory` | Read one memory (denials are audited) |
| `update_memory` | New version of a memory the agent owns |
| `share_memory` | Grant teams/agents (by name) read access |
| `memory_history` | Version history |
| `list_teams`, `whoami` | Discovery |

**Claude Code**

```bash
claude mcp add --transport http shared-memory http://localhost:3000/mcp --header "X-API-Key: $SML_API_KEY"
```

**Cursor** (`~/.cursor/mcp.json`)

```json
{ "mcpServers": { "shared-memory": { "url": "http://localhost:3000/mcp", "headers": { "X-API-Key": "sml_…" } } } }
```

**VS Code** (`.vscode/mcp.json`)

```json
{ "servers": { "shared-memory": { "type": "http", "url": "http://localhost:3000/mcp", "headers": { "X-API-Key": "sml_…" } } } }
```

**Claude Desktop** (`claude_desktop_config.json`, via the `mcp-remote` bridge, needs Node.js)

```json
{ "mcpServers": { "shared-memory": { "command": "npx", "args": ["-y", "mcp-remote", "http://localhost:3000/mcp", "--header", "X-API-Key:sml_…"] } } }
```

`sml mcp-config --client cursor|vscode|claude-code|claude-desktop` prints these for you. The server
also sends MCP *instructions* telling the model to search before a task and remember one concise
learning after it.

## 2. Python SDK

```bash
pip install ./sdk
```

```python
from shared_memory import MemoryClient
client = MemoryClient("http://localhost:3000", api_key="sml_…")
client.search("authentication failures caused by expired tokens", top_k=3)            # hybrid by default
client.search("ERR-7731", mode="keyword")                                             # exact identifiers
client.remember("JWT authentication issue", "Refresh token expired.", memory_type="learning")
```

See [sdk/README.md](../sdk/README.md).

## 3. TypeScript / JavaScript SDK

```bash
cd sdk-js && npm install && npm run build      # or: npm install ./sdk-js from your project
```

```ts
import { SharedMemoryClient } from "shared-memory-sdk";
const memory = new SharedMemoryClient({ baseUrl: "http://localhost:3000", apiKey: process.env.SML_API_KEY });
const { results } = await memory.search("authentication failures caused by expired tokens", { topK: 3 });
await memory.remember({ title: "Cache TTL decision", content: "Product cache TTL is 5 minutes.", memoryType: "decision" });
```

Zero dependencies (platform `fetch`): Node 18+, Deno, Bun, browsers. See [sdk-js/README.md](../sdk-js/README.md).

## 4. Command line (`sml`) — scripts and CI

Installed with the Python SDK.

```bash
export SML_URL=http://localhost:3000 SML_API_KEY=sml_…
sml whoami
sml search "why do users get logged out" -k 3
sml remember "Postmortem: checkout outage" - --type incident < postmortem.md
sml import ./docs ./faq.csv --tags handbook --skip-duplicates      # Markdown, JSON or CSV
```

## 5. Function-calling tools for any LLM

```python
from shared_memory import MemoryClient
from shared_memory.tools import MemoryToolkit

toolkit = MemoryToolkit(MemoryClient("http://localhost:3000", api_key="sml_…"))
tools = toolkit.anthropic_tools()        # or .openai_tools()
# … pass `tools` to the model; for every tool call:
result_json = toolkit.dispatch(name, arguments)
```

## 6. Importing existing knowledge

Real organisations already have runbooks, postmortems and FAQs. Three ways to load them:

| Where | How |
|---|---|
| Dashboard | Memory Explorer → **Import**: drop `.md`/`.txt`/`.json`/`.csv` files or paste Markdown, preview, import |
| CLI | `sml import <files or folders>` |
| API | `POST /api/memories/bulk` with up to 100 items per call |

Markdown is split into one memory per `##` section (focused memories retrieve better); long sections
are chunked below the size limit. Every item goes through validation, embedding, duplicate/conflict
detection and auditing; `skip_duplicates` makes re-imports idempotent. For raw logs or chat
transcripts set `detect_duplicates=false` — ~25 turns/s on a laptop.

## 7. Plain REST

Everything above is a thin layer over the documented REST API (`BASE/docs`):

```bash
curl -X POST http://localhost:3000/api/memories/search \
  -H "X-API-Key: $SML_API_KEY" -H "Content-Type: application/json" \
  -d '{"query": "authentication failures caused by expired tokens", "top_k": 3, "mode": "hybrid"}'
```
