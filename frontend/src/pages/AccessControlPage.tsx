import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Save, UserPlus } from "lucide-react";
import { useEffect, useState } from "react";
import { Alert, Badge, Button, Card, Field, Modal, PageHeader, Spinner, Tabs } from "@/components/ui";
import { useAuth } from "@/hooks/useAuth";
import { useAgents, useRoles, useTeams } from "@/hooks/useData";
import { api, errorMessage } from "@/services/api";
import type { Permission, Role, User } from "@/types";
import { timeAgo } from "@/utils/format";

type Tab = "roles" | "users" | "agents" | "visibility";

export default function AccessControlPage() {
  const [tab, setTab] = useState<Tab>("roles");
  return (
    <div className="stack-lg">
      <PageHeader
        title="Access Control"
        description="RBAC permissions are stored in the database and take effect on the next request. Per-memory visibility is layered on top of RBAC."
      />
      <Card>
        <Tabs<Tab>
          value={tab}
          onChange={setTab}
          tabs={[
            { id: "roles", label: "Roles & permissions" },
            { id: "users", label: "Users" },
            { id: "agents", label: "Agent roles" },
            { id: "visibility", label: "Memory visibility model" },
          ]}
        />
        {tab === "roles" && <RolesMatrix />}
        {tab === "users" && <UsersTab />}
        {tab === "agents" && <AgentRolesTab />}
        {tab === "visibility" && <VisibilityModel />}
      </Card>
    </div>
  );
}

function RolesMatrix() {
  const roles = useRoles();
  const perms = useQuery({ queryKey: ["permissions"], queryFn: () => api.get<Permission[]>("/api/permissions") });
  const { can } = useAuth();
  const qc = useQueryClient();
  const [draft, setDraft] = useState<Record<string, Set<string>>>({});
  useEffect(() => {
    if (roles.data) setDraft(Object.fromEntries(roles.data.map((r) => [r.id, new Set(r.permissions)])));
  }, [roles.data]);
  const save = useMutation({
    mutationFn: (role: Role) => api.put<Role>(`/api/roles/${role.id}/permissions`, { permissions: [...(draft[role.id] ?? [])] }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["roles"] }),
  });
  if (roles.isLoading || perms.isLoading) return <Spinner />;
  const editable = can("role:manage");
  const toggle = (roleId: string, code: string) =>
    setDraft((d) => {
      const next = new Set(d[roleId]);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return { ...d, [roleId]: next };
    });
  const dirty = (r: Role) => {
    const d = draft[r.id];
    return !!d && (d.size !== r.permissions.length || r.permissions.some((p) => !d.has(p)));
  };
  return (
    <div className="stack">
      {save.error && <Alert tone="danger">{errorMessage(save.error)}</Alert>}
      {save.isSuccess && <Alert tone="success">Permissions saved and audited (ROLE_PERMISSIONS_UPDATE).</Alert>}
      <div className="table-wrap">
        <table className="table matrix">
          <thead>
            <tr>
              <th>Permission</th>
              {roles.data!.map((r) => <th key={r.id}><Badge value={r.name}>{r.name}</Badge></th>)}
            </tr>
          </thead>
          <tbody>
            {perms.data!.map((p) => (
              <tr key={p.id}>
                <td><code>{p.code}</code><div className="subtle">{p.description}</div></td>
                {roles.data!.map((r) => (
                  <td key={r.id}>
                    <input
                      type="checkbox"
                      style={{ width: 16, height: 16, accentColor: "var(--primary)" }}
                      checked={draft[r.id]?.has(p.code) ?? false}
                      disabled={!editable || r.name === "ADMIN"}
                      onChange={() => toggle(r.id, p.code)}
                      aria-label={`${r.name} ${p.code}`}
                    />
                  </td>
                ))}
              </tr>
            ))}
            {editable && (
              <tr>
                <td className="subtle">ADMIN always keeps every permission.</td>
                {roles.data!.map((r) => (
                  <td key={r.id}>
                    {r.name !== "ADMIN" && (
                      <Button size="sm" variant={dirty(r) ? "primary" : "secondary"} disabled={!dirty(r)} icon={<Save size={13} />} loading={save.isPending && save.variables?.id === r.id} onClick={() => save.mutate(r)}>
                        Save
                      </Button>
                    )}
                  </td>
                ))}
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function UsersTab() {
  const { me } = useAuth();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<User[]>("/api/users") });
  const teams = useTeams();
  const roles = useRoles();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ username: "", full_name: "", email: "", password: "", role: "READ_ONLY", team_id: "" });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) => api.patch(`/api/users/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });
  const create = useMutation({
    mutationFn: () => api.post("/api/auth/register", { ...form, email: form.email || undefined, team_id: form.team_id || undefined }),
    onSuccess: () => { setOpen(false); setForm({ username: "", full_name: "", email: "", password: "", role: "READ_ONLY", team_id: "" }); qc.invalidateQueries({ queryKey: ["users"] }); },
  });
  if (users.isLoading) return <Spinner />;
  return (
    <div className="stack">
      <div className="row between">
        <span className="muted">Role changes are recorded as ROLE_CHANGE audit events.</span>
        <Button icon={<UserPlus size={15} />} onClick={() => setOpen(true)}>Add user</Button>
      </div>
      {update.error && <Alert tone="danger">{errorMessage(update.error)}</Alert>}
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>User</th><th>Role</th><th>Team</th><th>Active</th><th>Last login</th></tr></thead>
          <tbody>
            {users.data?.map((u) => (
              <tr key={u.id}>
                <td><strong>{u.username}</strong><div className="subtle">{u.full_name}{u.email ? ` · ${u.email}` : ""}</div></td>
                <td>
                  <select className="select" style={{ width: 160 }} value={u.role} disabled={u.id === me?.id} onChange={(e) => update.mutate({ id: u.id, body: { role: e.target.value } })}>
                    {roles.data?.map((r) => <option key={r.id} value={r.name}>{r.name}</option>)}
                  </select>
                </td>
                <td>
                  <select className="select" style={{ width: 160 }} value={u.team_id ?? ""} onChange={(e) => update.mutate({ id: u.id, body: e.target.value ? { team_id: e.target.value } : { clear_team: true } })}>
                    <option value="">No team</option>
                    {teams.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                  </select>
                </td>
                <td>
                  <label className="checkbox">
                    <input type="checkbox" checked={u.is_active} disabled={u.id === me?.id} onChange={(e) => update.mutate({ id: u.id, body: { is_active: e.target.checked } })} />
                    {u.is_active ? "Active" : "Disabled"}
                  </label>
                </td>
                <td className="subtle">{timeAgo(u.last_login_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Modal title="Add user" open={open} onClose={() => setOpen(false)}
        footer={<><Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button><Button loading={create.isPending} onClick={() => create.mutate()}>Create user</Button></>}>
        {create.error && <Alert tone="danger">{errorMessage(create.error)}</Alert>}
        <div className="form-grid">
          <Field label="Username"><input className="input" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></Field>
          <Field label="Full name"><input className="input" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></Field>
          <Field label="Email"><input className="input" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
          <Field label="Password" hint="≥10 chars, 3 of: lower, upper, digit, symbol"><input className="input" type="password" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></Field>
          <Field label="Role">
            <select className="select" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>{roles.data?.map((r) => <option key={r.id} value={r.name}>{r.name}</option>)}</select>
          </Field>
          <Field label="Team">
            <select className="select" value={form.team_id} onChange={(e) => setForm({ ...form, team_id: e.target.value })}>
              <option value="">No team</option>
              {teams.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </Field>
        </div>
      </Modal>
    </div>
  );
}

function AgentRolesTab() {
  const agents = useAgents();
  const roles = useRoles();
  const qc = useQueryClient();
  const setRoles = useMutation({
    mutationFn: ({ id, roles }: { id: string; roles: string[] }) => api.put(`/api/agents/${id}/roles`, { roles }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agents"] }),
  });
  if (agents.isLoading || roles.isLoading) return <Spinner />;
  return (
    <div className="stack">
      {setRoles.error && <Alert tone="danger">{errorMessage(setRoles.error)}</Alert>}
      <div className="table-wrap">
        <table className="table matrix">
          <thead><tr><th>Agent</th>{roles.data!.map((r) => <th key={r.id}>{r.name}</th>)}<th>Effective permissions</th></tr></thead>
          <tbody>
            {agents.data!.map((a) => (
              <tr key={a.id}>
                <td><strong>{a.name}</strong><div className="subtle">{a.team_name}</div></td>
                {roles.data!.map((r) => (
                  <td key={r.id}>
                    <input
                      type="checkbox"
                      style={{ width: 16, height: 16, accentColor: "var(--primary)" }}
                      checked={a.roles.includes(r.name)}
                      onChange={(e) => {
                        const next = e.target.checked ? [...a.roles, r.name] : a.roles.filter((x) => x !== r.name);
                        if (next.length) setRoles.mutate({ id: a.id, roles: next });
                      }}
                      aria-label={`${a.name} ${r.name}`}
                    />
                  </td>
                ))}
                <td style={{ textAlign: "left" }}><div className="row wrap gap-sm">{a.permissions.map((p) => <span key={p} className="tag">{p}</span>)}</div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function VisibilityModel() {
  const rows = [
    ["private", "Only the creating agent", "Never shared"],
    ["team", "All agents of the owning team", "Plus explicit team/agent grants"],
    ["selected_teams", "Owning team + allowed teams", "Grants managed with Share"],
    ["selected_agents", "Only explicitly allowed agents", "Not even the owning team"],
    ["organization", "Every authenticated agent", "Not allowed for confidential/restricted"],
  ];
  return (
    <div className="stack">
      <Alert tone="info" title="Enforced server-side, twice">
        Every read is checked against the memory row in PostgreSQL, and the same rule is compiled into the Qdrant filter so semantic search never scores a memory the
        caller may not read. Archived memories are visible to administrators only; expired memories are hidden unless explicitly requested.
      </Alert>
      <table className="table">
        <thead><tr><th>Visibility</th><th>Who can read</th><th>Notes</th></tr></thead>
        <tbody>{rows.map(([v, who, note]) => <tr key={v}><td><Badge value={v} /></td><td>{who}</td><td className="muted">{note}</td></tr>)}</tbody>
      </table>
    </div>
  );
}
