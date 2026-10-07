import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FolderKanban, Plus, Users } from "lucide-react";
import { useState } from "react";
import { Alert, Badge, Button, Card, Empty, Field, Modal, PageHeader, Spinner } from "@/components/ui";
import { useAuth } from "@/hooks/useAuth";
import { useProjects, useTeams } from "@/hooks/useData";
import { api, errorMessage } from "@/services/api";
import { VISIBILITIES, type Team, type Visibility } from "@/types";
import { formatDate, humanize } from "@/utils/format";

export default function TeamsPage() {
  const { can, isAdmin, me } = useAuth();
  const teams = useTeams();
  const projects = useProjects();
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [projectOpen, setProjectOpen] = useState(false);
  const [form, setForm] = useState({ name: "", description: "", default_visibility: "team" as Visibility, allow_cross_team_sharing: true });
  const [project, setProject] = useState({ name: "", description: "", team_id: "" });

  const create = useMutation({
    mutationFn: () => api.post<Team>("/api/teams", form),
    onSuccess: () => { setCreating(false); setForm({ name: "", description: "", default_visibility: "team", allow_cross_team_sharing: true }); qc.invalidateQueries({ queryKey: ["teams"] }); },
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<Team> }) => api.patch<Team>(`/api/teams/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["teams"] }),
  });
  const createProject = useMutation({
    mutationFn: () => api.post("/api/projects", { ...project, team_id: project.team_id || undefined }),
    onSuccess: () => { setProjectOpen(false); setProject({ name: "", description: "", team_id: "" }); qc.invalidateQueries({ queryKey: ["projects"] }); },
  });
  const manages = (id: string) => isAdmin || (me?.roles.includes("TEAM_MANAGER") && me.team_id === id);
  const teamName = (id: string | null) => teams.data?.find((t) => t.id === id)?.name ?? "—";

  return (
    <div className="stack-lg">
      <PageHeader
        title="Teams & Projects"
        description="Each team owns its memories. The access configuration sets the default visibility and whether memories may be shared across teams."
        actions={
          <>
            {can("team:update") && <Button variant="secondary" icon={<FolderKanban size={16} />} onClick={() => setProjectOpen(true)}>New project</Button>}
            {can("team:create") && <Button icon={<Plus size={16} />} onClick={() => setCreating(true)}>Create team</Button>}
          </>
        }
      />
      {update.error && <Alert tone="danger">{errorMessage(update.error)}</Alert>}
      <Card className="card-flush" title="Teams">
        {teams.isLoading ? <Spinner /> : !teams.data?.length ? <Empty title="No teams yet" icon={<Users size={26} />} /> : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>Team</th><th>Agents</th><th>Members</th><th>Memories</th><th>Default visibility</th><th>Cross-team sharing</th><th>Created</th></tr>
              </thead>
              <tbody>
                {teams.data.map((t) => (
                  <tr key={t.id}>
                    <td><strong>{t.name}</strong><div className="subtle">{t.description}</div></td>
                    <td>{t.agent_count}</td>
                    <td>{t.member_count}</td>
                    <td><strong>{t.memory_count}</strong></td>
                    <td>
                      {manages(t.id) ? (
                        <select className="select" style={{ width: 170 }} value={t.default_visibility} onChange={(e) => update.mutate({ id: t.id, body: { default_visibility: e.target.value as Visibility } })}>
                          {VISIBILITIES.map((v) => <option key={v} value={v}>{humanize(v)}</option>)}
                        </select>
                      ) : <Badge value={t.default_visibility} />}
                    </td>
                    <td>
                      {manages(t.id) ? (
                        <label className="checkbox">
                          <input type="checkbox" checked={t.allow_cross_team_sharing} onChange={(e) => update.mutate({ id: t.id, body: { allow_cross_team_sharing: e.target.checked } })} />
                          {t.allow_cross_team_sharing ? "Allowed" : "Blocked"}
                        </label>
                      ) : <Badge tone={t.allow_cross_team_sharing ? "green" : "red"}>{t.allow_cross_team_sharing ? "Allowed" : "Blocked"}</Badge>}
                    </td>
                    <td className="subtle nowrap">{formatDate(t.created_at, false)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card className="card-flush" title="Projects" subtitle="Memories can be scoped to a project and filtered by it during search">
        {!projects.data?.length ? <Empty title="No projects" /> : (
          <table className="table">
            <thead><tr><th>Project</th><th>Owning team</th><th>Memories</th><th>Created</th></tr></thead>
            <tbody>
              {projects.data.map((p) => (
                <tr key={p.id}>
                  <td><strong>{p.name}</strong><div className="subtle">{p.description}</div></td>
                  <td>{teamName(p.team_id)}</td>
                  <td>{p.memory_count}</td>
                  <td className="subtle">{formatDate(p.created_at, false)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Modal title="Create team" open={creating} onClose={() => setCreating(false)}
        footer={<><Button variant="secondary" onClick={() => setCreating(false)}>Cancel</Button><Button loading={create.isPending} disabled={!form.name} onClick={() => create.mutate()}>Create</Button></>}>
        {create.error && <Alert tone="danger">{errorMessage(create.error)}</Alert>}
        <Field label="Name"><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Development" /></Field>
        <Field label="Description"><textarea className="textarea" style={{ minHeight: 70 }} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
        <Field label="Default memory visibility">
          <select className="select" value={form.default_visibility} onChange={(e) => setForm({ ...form, default_visibility: e.target.value as Visibility })}>
            {VISIBILITIES.map((v) => <option key={v} value={v}>{humanize(v)}</option>)}
          </select>
        </Field>
        <label className="checkbox"><input type="checkbox" checked={form.allow_cross_team_sharing} onChange={(e) => setForm({ ...form, allow_cross_team_sharing: e.target.checked })} /> Allow sharing memories with other teams</label>
      </Modal>

      <Modal title="Create project" open={projectOpen} onClose={() => setProjectOpen(false)}
        footer={<><Button variant="secondary" onClick={() => setProjectOpen(false)}>Cancel</Button><Button loading={createProject.isPending} disabled={!project.name} onClick={() => createProject.mutate()}>Create</Button></>}>
        {createProject.error && <Alert tone="danger">{errorMessage(createProject.error)}</Alert>}
        <Field label="Name"><input className="input" value={project.name} onChange={(e) => setProject({ ...project, name: e.target.value })} /></Field>
        <Field label="Description"><input className="input" value={project.description} onChange={(e) => setProject({ ...project, description: e.target.value })} /></Field>
        <Field label="Owning team">
          <select className="select" value={project.team_id} onChange={(e) => setProject({ ...project, team_id: e.target.value })}>
            <option value="">{isAdmin ? "None" : "My team"}</option>
            {teams.data?.filter((t) => manages(t.id)).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </Field>
      </Modal>
    </div>
  );
}
