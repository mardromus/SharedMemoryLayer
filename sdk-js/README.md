# shared-memory-sdk (TypeScript / JavaScript)

Zero-dependency client for the **Shared Memory Layer** — works in Node 18+, Deno, Bun and browsers
(platform `fetch`).

```bash
cd sdk-js && npm install && npm run build     # builds dist/
cd your-agent-project && npm install ../path/to/shared-memory-layer/sdk-js
```

```ts
import { SharedMemoryClient, SharedMemoryError } from "shared-memory-sdk";

const memory = new SharedMemoryClient({ baseUrl: "http://localhost:8000", apiKey: process.env.SML_API_KEY! });

// reuse what other teams already know (hybrid: meaning + exact keywords)
const { results } = await memory.search("authentication failures caused by expired tokens", { topK: 3 });
for (const r of results) console.log(r.similarity.toFixed(3), r.team, r.title, r.match);

// store what you learned
const { memory: m, warnings } = await memory.remember({
  title: "JWT authentication issue",
  content: "Refresh token expired.",
  memoryType: "learning",
  tags: ["authentication", "jwt"],
});
if (warnings.message) console.warn(warnings.message); // e.g. "Potential duplicate memories found."

try {
  await memory.get("some-confidential-memory-id");
} catch (err) {
  if (err instanceof SharedMemoryError && err.isPermissionDenied) console.log("denied:", err.message);
}
```

| Method | |
|---|---|
| `search(query, { topK, mode, similarityThreshold, memoryTypes, teamIds, projectIds, tags, includeExpired })` | hybrid / semantic / keyword search |
| `remember({ title, content, memoryType, tags, visibility, projectId, … })` | store a memory |
| `rememberMany(items, { skipDuplicates, detectDuplicates })` | bulk import (batched by 100) |
| `get(id)`, `update(id, fields, { changeDescription, expectedVersion })`, `versions(id)` | read / new version / history |
| `share(id, { teamIds, agentIds })`, `unshare(id, "team:<id>")` | sharing grants |
| `runTask(task, { topK, storeResult })`, `whoami()` | server-side agent runtime, identity |

Errors are `SharedMemoryError` (`status`, `code`, `requestId`, `details`). `429` (honouring
`Retry-After`), `502/503/504` and network errors are retried with back-off.

```bash
npm test                                             # unit tests (mocked fetch)
SML_URL=http://localhost:8000 SML_API_KEY=sml_… npm test   # + live test against a running stack
```
