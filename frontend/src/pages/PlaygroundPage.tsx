import { useMutation, useQuery } from "@tanstack/react-query";
import { Bot, BrainCircuit, DatabaseZap, ShieldCheck, ShieldX, Sparkles } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { MemoryForm, SearchResultCard, toMemoryPayload, WarningsPanel } from "@/components/memory";
import { Alert, Badge, Button, Card, Empty, Field, PageHeader, Spinner, Tabs } from "@/components/ui";
import { useAgents } from "@/hooks/useData";
import { api, errorMessage } from "@/services/api";
import type { AccessCheckResult, AgentTaskResponse, MemoryCreateResponse, MemoryListItem, Page, SearchResponse } from "@/types";
import { humanize } from "@/utils/format";

type Tab = "search" | "ask" | "store" | "access";

const EXAMPLES = [
  "Have we previously encountered authentication problems?",
  "authentication failures caused by expired tokens",
  "which database does the customer portal use",
  "how should failed payment webhooks be retried",
];

export default function PlaygroundPage() {
  const agents = useAgents();
  const [agentId, setAgentId] = useState("");
  const [tab, setTab] = useState<Tab>("search");
  const agent = useMemo(() => agents.data?.find((a) => a.id === agentId), [agents.data, agentId]);

  if (agents.isLoading) return <Spinner />;
  const active = agents.data?.filter((a) => a.status === "active") ?? [];

  return (
    <div className="stack-lg">
      <PageHeader
        title="Agent Playground"
        description="Act as an agent: every request runs with that agent's identity and permissions (never yours) and is audited as the agent, on your behalf."
      />
      <Card>
        <div className="grid grid-3">
          <Field label="Simulated agent">
            <select className="select" value={agentId} onChange={(e) => setAgentId(e.target.value)}>
              <option value="">Select an agent…</option>
              {active.map((a) => <option key={a.id} value={a.id}>{a.name} — {a.team_name}</option>)}
            </select>
          </Field>
          {agent && (
            <div className="span-2 row gap wrap" style={{ alignItems: "center" }}>
              <div className="avatar" style={{ background: "var(--primary-50)", color: "var(--primary)", width: 42, height: 42 }}><Bot size={20} /></div>
              <div>
                <strong>{agent.name}</strong> <span className="muted">· team {agent.team_name} · {agent.agent_type}</span>
                <div className="row wrap gap-sm" style={{ marginTop: 4 }}>
                  {agent.roles.map((r) => <Badge key={r} value={r}>{r}</Badge>)}
                  {agent.permissions.map((p) => <span key={p} className="tag">{p}</span>)}
                </div>
              </div>
            </div>
          )}
        </div>
      </Card>

      {!agent ? (
        <Card><Empty title="Choose an agent to start" icon={<Bot size={28} />}>Pick e.g. QA-Agent and ask about authentication problems — the result will include knowledge written by other teams only if QA is authorised to read it.</Empty></Card>
      ) : (
        <Card>
          <Tabs<Tab>
            value={tab}
            onChange={setTab}
            tabs={[
              { id: "search", label: <span className="row gap-sm"><Sparkles size={15} /> Semantic search</span> },
              { id: "ask", label: <span className="row gap-sm"><BrainCircuit size={15} /> Ask agent (LLM)</span> },
              { id: "store", label: <span className="row gap-sm"><DatabaseZap size={15} /> Store memory</span> },
              { id: "access", label: <span className="row gap-sm"><ShieldCheck size={15} /> Access test</span> },
            ]}
          />
          {tab === "search" && <SearchTab agentId={agent.id} />}
          {tab === "ask" && <AskTab agentId={agent.id} />}
          {tab === "store" && <StoreTab agentId={agent.id} />}
          {tab === "access" && <AccessTab agentId={agent.id} agentName={agent.name} />}
        </Card>
      )}
    </div>
  );
}

function SearchTab({ agentId }: { agentId: string }) {
  const [query, setQuery] = useState(EXAMPLES[0]);
  const [topK, setTopK] = useState(5);
  const [threshold, setThreshold] = useState(0.55);
  const search = useMutation({
    mutationFn: () => api.post<SearchResponse>("/api/playground/search", { agent_id: agentId, search: { query, top_k: topK, similarity_threshold: threshold } }),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); search.mutate(); };
  return (
    <div className="stack">
      <form className="stack" onSubmit={submit}>
        <div className="row gap">
          <input className="input grow" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Ask in natural language" style={{ height: 42 }} />
          <Button type="submit" loading={search.isPending} disabled={!query.trim()}>Search memory</Button>
        </div>
        <div className="row gap wrap">
          {EXAMPLES.map((q) => <button type="button" key={q} className="btn btn-ghost btn-sm" onClick={() => setQuery(q)}>{q}</button>)}
        </div>
        <div className="grid grid-2">
          <Field label={`Top-K · ${topK}`}><input className="range" type="range" min={1} max={20} value={topK} onChange={(e) => setTopK(Number(e.target.value))} /></Field>
          <Field label={`Similarity threshold · ${threshold.toFixed(2)}`}><input className="range" type="range" min={0} max={0.95} step={0.05} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} /></Field>
        </div>
      </form>
      {search.error && <Alert tone="danger">{errorMessage(search.error)}</Alert>}
      {search.data && (
        <>
          <div className="steps">
            <span className="step">1 · Embed query</span>
            <span className="step">2 · Qdrant similarity search</span>
            <span className="step">3 · Permission + metadata filter</span>
            <span className="step">4 · Authoritative re-check</span>
            <span className="step">5 · Rank & top-{topK}</span>
            <span className="muted">{search.data.total} results in {search.data.took_ms} ms ({search.data.mode})</span>
          </div>
          {search.data.results.length === 0 ? <Empty title="No authorised memory above the threshold" /> : search.data.results.map((r, i) => <SearchResultCard key={r.memory_id} r={r} rank={i + 1} />)}
        </>
      )}
    </div>
  );
}

function AskTab({ agentId }: { agentId: string }) {
  const [task, setTask] = useState("Write test cases for authentication failures caused by expired tokens");
  const [store, setStore] = useState(false);
  const run = useMutation({
    mutationFn: () => api.post<AgentTaskResponse>("/api/playground/tasks", { agent_id: agentId, task: { task, top_k: 5, store_result: store } }),
  });
  return (
    <div className="stack">
      <Field label="Task for the agent" hint="Task → memory search → relevant memories as context → LLM → response → optional memory creation">
        <textarea className="textarea" style={{ minHeight: 80 }} value={task} onChange={(e) => setTask(e.target.value)} />
      </Field>
      <div className="row between">
        <label className="checkbox"><input type="checkbox" checked={store} onChange={(e) => setStore(e.target.checked)} /> Store the result as a new memory (task_result)</label>
        <Button loading={run.isPending} disabled={task.trim().length < 3} onClick={() => run.mutate()}>Run task</Button>
      </div>
      {run.error && <Alert tone="danger">{errorMessage(run.error)}</Alert>}
      {run.data && (
        <div className="stack">
          <div className="steps">
            {run.data.steps.map((s) => <span key={s.name} className="step">{humanize(s.name)} · {s.took_ms} ms</span>)}
            <span className="muted">total {run.data.took_ms} ms</span>
          </div>
          <div className="row gap-sm">
            <Badge tone="violet">provider: {run.data.provider}{run.data.model ? ` · ${run.data.model}` : ""}</Badge>
            <Badge tone="green">{run.data.memories_used.length} memories used · {run.data.reused_cross_team} from other teams</Badge>
          </div>
          {run.data.provider === "extractive" && (
            <Alert tone="info">No LLM is configured (LLM_PROVIDER=none), so the answer is an extractive synthesis of the retrieved memories. Set LLM_PROVIDER=ollama/openai/anthropic for generated answers.</Alert>
          )}
          {run.data.fallback_reason && <Alert tone="warning" title="LLM call failed — fell back to extractive synthesis">{run.data.fallback_reason}</Alert>}
          <div className="answer">{run.data.answer}</div>
          {run.data.stored_memory_id && (
            <Alert tone="success" title="Result stored as a new memory">
              <Link to={`/memories/${run.data.stored_memory_id}`}>Open the stored memory</Link>
            </Alert>
          )}
          {run.data.store_warnings && run.data.stored_memory_id && <WarningsPanel warnings={run.data.store_warnings} />}
          <strong>Context retrieved from shared memory</strong>
          {run.data.memories_used.map((r, i) => <SearchResultCard key={r.memory_id} r={r} rank={i + 1} />)}
        </div>
      )}
    </div>
  );
}

function StoreTab({ agentId }: { agentId: string }) {
  const create = useMutation({
    mutationFn: (payload: ReturnType<typeof toMemoryPayload>) => api.post<MemoryCreateResponse>("/api/playground/memories", { agent_id: agentId, memory: payload }),
  });
  return (
    <div className="stack">
      {create.error && <Alert tone="danger">{errorMessage(create.error)}</Alert>}
      {create.data && (
        <div className="stack">
          <Alert tone="success" title={`Stored as “${create.data.memory.title}” (v${create.data.memory.version})`}>
            Embedding generated with {create.data.memory.embedding_model} and stored in Qdrant · <Link to={`/memories/${create.data.memory.id}`}>open memory</Link>
          </Alert>
          <WarningsPanel warnings={create.data.warnings} />
        </div>
      )}
      <MemoryForm key={create.data?.memory.id ?? "new"} busy={create.isPending} submitLabel="Store as agent" onSubmit={(v) => create.mutate(toMemoryPayload(v))} />
    </div>
  );
}

function AccessTab({ agentId, agentName }: { agentId: string; agentName: string }) {
  const [memoryId, setMemoryId] = useState("");
  const memories = useQuery({ queryKey: ["all-memories-for-access"], queryFn: () => api.get<Page<MemoryListItem>>("/api/memories", { page_size: 200 }) });
  const check = useMutation({ mutationFn: () => api.post<AccessCheckResult>("/api/playground/access-check", { agent_id: agentId, memory_id: memoryId }) });
  const selected = memories.data?.items.find((m) => m.id === memoryId);
  return (
    <div className="stack">
      <Alert tone="info">
        {agentName} tries to open a memory directly. The API evaluates RBAC and the memory's visibility; denials are returned as 403 and written to the audit log.
      </Alert>
      <div className="row gap">
        <select className="select grow" value={memoryId} onChange={(e) => { setMemoryId(e.target.value); check.reset(); }}>
          <option value="">Select a memory…</option>
          {memories.data?.items.map((m) => (
            <option key={m.id} value={m.id}>
              [{m.team.name}] {m.title} — {humanize(m.visibility)}{m.sensitivity === "confidential" || m.sensitivity === "restricted" ? ` · ${m.sensitivity.toUpperCase()}` : ""}
            </option>
          ))}
        </select>
        <Button disabled={!memoryId} loading={check.isPending} onClick={() => check.mutate()}>Attempt access</Button>
      </div>
      {selected && (
        <div className="row gap-sm">
          <span className="muted">Target:</span> <Badge value={selected.visibility} /> <Badge value={selected.sensitivity} /> <span className="muted">owned by {selected.team.name}</span>
        </div>
      )}
      {check.error && <Alert tone="danger">{errorMessage(check.error)}</Alert>}
      {check.data && (
        <div className={`decision-banner ${check.data.decision === "ALLOWED" ? "decision-allowed" : "decision-denied"}`}>
          {check.data.decision === "ALLOWED" ? <ShieldCheck size={44} /> : <ShieldX size={44} />}
          <div className="stack" style={{ gap: 4 }}>
            <div className="decision-title">{check.data.decision === "ALLOWED" ? "ACCESS ALLOWED" : "ACCESS DENIED"}</div>
            <div>
              {check.data.agent} ({check.data.agent_team}) → memory #{check.data.memory_id.slice(0, 8)}
              {check.data.decision === "DENIED" && <> · Reason: <strong>{check.data.reason}</strong></>}
            </div>
            <div className="subtle" style={{ color: "inherit" }}>
              Recorded in the audit log. <Link to="/audit">View audit log →</Link>
            </div>
          </div>
        </div>
      )}
      {check.data?.memory && (
        <Card title={check.data.memory.title}>
          <p className="memory-content" style={{ margin: 0 }}>{check.data.memory.content}</p>
        </Card>
      )}
    </div>
  );
}
