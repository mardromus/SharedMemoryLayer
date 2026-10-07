import { useMutation, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plug } from "lucide-react";
import { useMemo, useState } from "react";
import { CopyButton } from "@/components/memory";
import { Alert, Button, Card, Empty, Field, PageHeader, Tabs } from "@/components/ui";
import { useAuth } from "@/hooks/useAuth";
import { useAgents } from "@/hooks/useData";
import { api, errorMessage } from "@/services/api";

type Tab = "claude-code" | "cursor" | "vscode" | "claude-desktop" | "python" | "typescript" | "cli" | "tools" | "curl";

const TABS: { id: Tab; label: string; lang: string; hint: string }[] = [
  { id: "claude-code", label: "Claude Code", lang: "bash", hint: "Registers the shared memory as an MCP server. The agent gets search_memory, remember, get_memory, update_memory, share_memory and more." },
  { id: "cursor", label: "Cursor", lang: "json", hint: "Add to ~/.cursor/mcp.json (or .cursor/mcp.json in a project)." },
  { id: "vscode", label: "VS Code", lang: "json", hint: "Add to .vscode/mcp.json (GitHub Copilot agent mode)." },
  { id: "claude-desktop", label: "Claude Desktop", lang: "json", hint: "Add to claude_desktop_config.json. Uses the mcp-remote bridge (needs Node.js)." },
  { id: "python", label: "Python SDK", lang: "python", hint: "pip install ./sdk  (from this repository)" },
  { id: "typescript", label: "TypeScript", lang: "ts", hint: "npm install ./sdk-js  (Node 18+, Deno, Bun or the browser)" },
  { id: "cli", label: "CLI", lang: "bash", hint: "The sml command ships with the Python SDK — handy for scripts and CI pipelines." },
  { id: "tools", label: "LLM tools", lang: "python", hint: "Ready-made tool definitions for any function-calling model (OpenAI or Anthropic format)." },
  { id: "curl", label: "REST / curl", lang: "bash", hint: "Plain HTTP works from any language. Full reference at /docs." },
];

function snippet(tab: Tab, base: string, key: string): string {
  const mcp = `${base}/mcp`;
  switch (tab) {
    case "claude-code":
      return `claude mcp add --transport http shared-memory ${mcp} \\\n  --header "X-API-Key: ${key}"`;
    case "cursor":
      return JSON.stringify({ mcpServers: { "shared-memory": { url: mcp, headers: { "X-API-Key": key } } } }, null, 2);
    case "vscode":
      return JSON.stringify({ servers: { "shared-memory": { type: "http", url: mcp, headers: { "X-API-Key": key } } } }, null, 2);
    case "claude-desktop":
      return JSON.stringify({ mcpServers: { "shared-memory": { command: "npx", args: ["-y", "mcp-remote", mcp, "--header", `X-API-Key:${key}`] } } }, null, 2);
    case "python":
      return `from shared_memory import MemoryClient

client = MemoryClient("${base}", api_key="${key}")

# 1. reuse what other teams already know
for r in client.search("authentication failures caused by expired tokens", top_k=3):
    print(r.similarity, r.team, r.title)

# 2. store what you learned
client.remember("JWT authentication issue", "Refresh token expired.", memory_type="learning",
                tags=["authentication", "jwt"])`;
    case "typescript":
      return `import { SharedMemoryClient } from "shared-memory-sdk";

const memory = new SharedMemoryClient({ baseUrl: "${base}", apiKey: "${key}" });

const { results } = await memory.search("authentication failures caused by expired tokens", { topK: 3 });
results.forEach((r) => console.log(r.similarity, r.team, r.title));

await memory.remember({ title: "JWT authentication issue", content: "Refresh token expired.", memoryType: "learning" });`;
    case "cli":
      return `export SML_URL=${base}
export SML_API_KEY=${key}

sml whoami
sml search "authentication failures caused by expired tokens" -k 3
sml remember "Cache TTL decision" "Product cache TTL is 5 minutes" --type decision --tags cache
sml import ./docs --tags runbook --skip-duplicates      # Markdown / JSON / CSV`;
    case "tools":
      return `from shared_memory import MemoryClient
from shared_memory.tools import MemoryToolkit

toolkit = MemoryToolkit(MemoryClient("${base}", api_key="${key}"))
tools = toolkit.anthropic_tools()   # or toolkit.openai_tools()

# pass \`tools\` to your model; for every tool call it makes:
result_json = toolkit.dispatch(tool_call.name, tool_call.input)`;
    case "curl":
      return `curl -X POST ${base}/api/memories/search \\
  -H "X-API-Key: ${key}" -H "Content-Type: application/json" \\
  -d '{"query": "authentication failures caused by expired tokens", "top_k": 3}'`;
  }
}

export default function ConnectPage() {
  const { isAdmin, me } = useAuth();
  const agents = useAgents();
  const qc = useQueryClient();
  const [agentId, setAgentId] = useState("");
  const [tab, setTab] = useState<Tab>("claude-code");
  const [key, setKey] = useState<string | null>(null);
  const base = typeof window !== "undefined" ? window.location.origin : "http://localhost:3000";
  const manageable = useMemo(
    () => (agents.data ?? []).filter((a) => a.status === "active" && (isAdmin || (me?.roles.includes("TEAM_MANAGER") && me.team_id === a.team_id))),
    [agents.data, isAdmin, me],
  );
  const agent = manageable.find((a) => a.id === agentId);
  const issue = useMutation({
    mutationFn: () => api.post<{ api_key: string }>(`/api/agents/${agentId}/api-keys`, { name: `connect-${tab}` }),
    onSuccess: (k) => { setKey(k.api_key); qc.invalidateQueries({ queryKey: ["agents"] }); },
  });
  const current = TABS.find((t) => t.id === tab)!;
  const code = snippet(tab, base, key ?? "<AGENT_API_KEY>");

  return (
    <div className="stack-lg">
      <PageHeader
        title="Connect an agent"
        description="Give any AI agent or tool access to the shared memory with its own identity. It can only read what its team is allowed to see, and everything it does is audited."
      />
      <Card>
        <div className="grid grid-3">
          <Field label="Agent identity">
            <select className="select" value={agentId} onChange={(e) => { setAgentId(e.target.value); setKey(null); }}>
              <option value="">Select an agent…</option>
              {manageable.map((a) => <option key={a.id} value={a.id}>{a.name} — {a.team_name}</option>)}
            </select>
          </Field>
          <div className="span-2 row gap wrap" style={{ alignItems: "flex-end" }}>
            <Button icon={<KeyRound size={16} />} disabled={!agentId} loading={issue.isPending} onClick={() => issue.mutate()}>
              Generate API key
            </Button>
            <span className="subtle">{agent ? `${agent.name} · ${agent.permissions.length} permissions · team ${agent.team_name}` : "Keys are shown once and stored hashed. Revoke them on the Agents page."}</span>
          </div>
        </div>
        {issue.error && <div style={{ marginTop: 12 }}><Alert tone="danger">{errorMessage(issue.error)}</Alert></div>}
        {key && (
          <div style={{ marginTop: 12 }}>
            <Alert tone="success" title="API key created — it is already filled into the snippets below. Copy it now; it will not be shown again.">
              <div className="secret-box" style={{ marginTop: 6 }}>
                <span className="grow">{key}</span>
                <CopyButton text={key} />
              </div>
            </Alert>
          </div>
        )}
      </Card>

      {!manageable.length && !agents.isLoading ? (
        <Card><Empty title="No agents you can manage" icon={<Plug size={26} />}>Register an agent on the Agents page first.</Empty></Card>
      ) : (
        <Card>
          <Tabs<Tab> value={tab} onChange={setTab} tabs={TABS.map((t) => ({ id: t.id, label: t.label }))} />
          <p className="muted" style={{ marginTop: 0 }}>{current.hint}</p>
          <div className="code-block">
            <div className="code-toolbar">
              <span className="subtle">{current.lang}</span>
              <CopyButton text={code} />
            </div>
            <pre><code>{code}</code></pre>
          </div>
          <p className="subtle" style={{ marginBottom: 0 }}>
            Server URL: <code>{base}</code> · MCP endpoint: <code>{base}/mcp</code> · API reference: <a href="/docs" target="_blank" rel="noreferrer">/docs</a>
          </p>
        </Card>
      )}
    </div>
  );
}
