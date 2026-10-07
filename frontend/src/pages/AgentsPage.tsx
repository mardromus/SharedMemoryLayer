import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Bot, KeyRound, Plus, Trash2 } from "lucide-react";
import { Fragment, useState, type FormEvent } from "react";
import { CopyButton } from "@/components/memory";
import { Alert, Badge, Button, Card, Empty, Field, Modal, PageHeader, Spinner } from "@/components/ui";
import { useAuth } from "@/hooks/useAuth";
import { useAgents, useTeams } from "@/hooks/useData";
import { api, errorMessage } from "@/services/api";
import type { Agent, ApiKey } from "@/types";
import { formatDate, timeAgo } from "@/utils/format";

export default function AgentsPage() {
  const { can, isAdmin, me } = useAuth();
  const agents = useAgents();
  const teams = useTeams();
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<{ agent: string; key: string } | null>(null);
  const [form, setForm] = useState({ name: "", team_id: "", agent_type: "custom", description: "", capabilities: "", role: "AGENT" });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["agents"] });
  const create = useMutation({
    mutationFn: () =>
      api.post<Agent>("/api/agents", {
        name: form.name,
        team_id: form.team_id,
        agent_type: form.agent_type,
        description: form.description,
        capabilities: form.capabilities.split(",").map((c) => c.trim()).filter(Boolean),
        roles: [form.role],
      }),
    onSuccess: (a) => {
      setCreating(false);
      setSecret({ agent: a.name, key: a.api_key ?? "" });
      setForm({ name: "", team_id: "", agent_type: "custom", description: "", capabilities: "", role: "AGENT" });
      invalidate();
    },
  });
  const newKey = useMutation({
    mutationFn: (a: Agent) => api.post<ApiKey & { api_key: string }>(`/api/agents/${a.id}/api-keys`, { name: `key-${new Date().toISOString().slice(0, 10)}` }).then((k) => ({ a, k })),
    onSuccess: ({ a, k }) => { setSecret({ agent: a.name, key: k.api_key }); invalidate(); },
  });
  const revoke = useMutation({ mutationFn: ({ a, k }: { a: Agent; k: ApiKey }) => api.del(`/api/agents/${a.id}/api-keys/${k.id}`), onSuccess: invalidate });
  const setStatus = useMutation({ mutationFn: ({ a, status }: { a: Agent; status: string }) => api.patch(`/api/agents/${a.id}`, { status }), onSuccess: invalidate });
  const remove = useMutation({ mutationFn: (a: Agent) => api.del(`/api/agents/${a.id}`), onSuccess: invalidate });
  const actionError = newKey.error || revoke.error || setStatus.error || remove.error;

  const manages = (teamId: string) => isAdmin || (me?.roles.includes("TEAM_MANAGER") && me.team_id === teamId);

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate();
  }

  return (
    <div className="stack-lg">
      <PageHeader
        title="Agents"
        description="Every AI agent has its own identity, team, roles and API keys. Keys are stored hashed and shown only once."
        actions={can("agent:create") && <Button icon={<Plus size={16} />} onClick={() => setCreating(true)}>Register agent</Button>}
      />
      {secret && (
        <Alert tone="success" title={`API key for ${secret.agent} — copy it now, it will not be shown again`} onClose={() => setSecret(null)}>
          <div className="secret-box" style={{ marginTop: 6 }}>
            <span className="grow">{secret.key}</span>
            <CopyButton text={secret.key} />
          </div>
        </Alert>
      )}
      {actionError && <Alert tone="danger">{errorMessage(actionError)}</Alert>}
      <Card className="card-flush">
        {agents.isLoading ? <Spinner /> : !agents.data?.length ? <Empty title="No agents registered" icon={<Bot size={26} />} /> : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>Agent</th><th>Team</th><th>Status</th><th>Roles</th><th>Permissions</th><th>Last active</th><th>Memories</th><th /></tr>
              </thead>
              <tbody>
                {agents.data.map((a) => (
                  <Fragment key={a.id}>
                    <tr className="clickable" onClick={() => setExpanded(expanded === a.id ? null : a.id)}>
                      <td>
                        <div className="row gap-sm">
                          <div className="avatar" style={{ background: "var(--primary-50)", color: "var(--primary)" }}><Bot size={16} /></div>
                          <div>
                            <strong>{a.name}</strong>
                            <div className="subtle">{a.agent_type}{a.capabilities.length ? ` · ${a.capabilities.join(", ")}` : ""}</div>
                          </div>
                        </div>
                      </td>
                      <td>{a.team_name}</td>
                      <td><Badge value={a.status} /></td>
                      <td><div className="row gap-sm">{a.roles.map((r) => <Badge key={r} value={r}>{r}</Badge>)}</div></td>
                      <td className="subtle" title={a.permissions.join("\n")}>{a.permissions.length} permissions</td>
                      <td className="nowrap subtle" title={formatDate(a.last_active_at)}>{timeAgo(a.last_active_at)}</td>
                      <td><strong>{a.memories_created}</strong></td>
                      <td className="nowrap" onClick={(e) => e.stopPropagation()}>
                        {manages(a.team_id) && (
                          <div className="row gap-sm">
                            <Button size="sm" variant="secondary" icon={<KeyRound size={14} />} onClick={() => newKey.mutate(a)}>New key</Button>
                            <Button size="sm" variant="ghost" onClick={() => setStatus.mutate({ a, status: a.status === "active" ? "suspended" : "active" })}>
                              {a.status === "active" ? "Suspend" : "Activate"}
                            </Button>
                            {can("agent:delete") && (
                              <button className="icon-btn" title="Delete agent" aria-label={`Delete ${a.name}`} onClick={() => window.confirm(`Delete ${a.name}? Its API keys are revoked; its memories keep their provenance.`) && remove.mutate(a)}>
                                <Trash2 size={15} />
                              </button>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                    {expanded === a.id && (
                      <tr>
                        <td colSpan={8} style={{ background: "var(--surface-2)" }}>
                          <div className="grid grid-2">
                            <div className="stack">
                              <strong>Permissions</strong>
                              <div className="row wrap gap-sm">{a.permissions.map((p) => <span key={p} className="tag">{p}</span>)}</div>
                              {a.description && <p className="muted" style={{ margin: 0 }}>{a.description}</p>}
                              {a.persona && <p className="subtle" style={{ margin: 0 }}>Persona: {a.persona}</p>}
                            </div>
                            <div className="stack">
                              <strong>API keys</strong>
                              {!manages(a.team_id) ? <span className="muted">Visible to the team's managers.</span> : a.api_keys.length === 0 ? <span className="muted">No keys issued.</span> : a.api_keys.map((k) => (
                                <div key={k.id} className="row between">
                                  <span><code>sml_{k.prefix}_…</code> <span className="subtle">{k.name} · created {formatDate(k.created_at, false)} · used {timeAgo(k.last_used_at)}</span></span>
                                  {k.revoked_at ? <Badge tone="slate">revoked</Badge> : <Button size="sm" variant="ghost" onClick={() => revoke.mutate({ a, k })}>Revoke</Button>}
                                </div>
                              ))}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Modal title="Register agent" open={creating} onClose={() => setCreating(false)}
        footer={<><Button variant="secondary" onClick={() => setCreating(false)}>Cancel</Button><Button loading={create.isPending} onClick={() => create.mutate()} disabled={!form.name || !form.team_id}>Register</Button></>}>
        {create.error && <Alert tone="danger">{errorMessage(create.error)}</Alert>}
        <form className="form-grid" onSubmit={submit}>
          <Field label="Name"><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Dev-Agent" required /></Field>
          <Field label="Team">
            <select className="select" value={form.team_id} onChange={(e) => setForm({ ...form, team_id: e.target.value })} required>
              <option value="">Select team…</option>
              {teams.data?.filter((t) => manages(t.id)).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </Field>
          <Field label="Agent type">
            <select className="select" value={form.agent_type} onChange={(e) => setForm({ ...form, agent_type: e.target.value })}>
              {["development", "testing", "security", "support", "custom"].map((t) => <option key={t}>{t}</option>)}
            </select>
          </Field>
          <Field label="Role">
            <select className="select" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              <option value="AGENT">AGENT</option>
              <option value="READ_ONLY">READ_ONLY</option>
              {isAdmin && <option value="TEAM_MANAGER">TEAM_MANAGER</option>}
            </select>
          </Field>
          <div className="full"><Field label="Capabilities" hint="Comma separated"><input className="input" value={form.capabilities} onChange={(e) => setForm({ ...form, capabilities: e.target.value })} placeholder="coding, architecture" /></Field></div>
          <div className="full"><Field label="Description"><textarea className="textarea" style={{ minHeight: 70 }} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field></div>
        </form>
      </Modal>
    </div>
  );
}
