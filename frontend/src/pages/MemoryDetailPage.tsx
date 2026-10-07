import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArrowLeft, CheckCircle2, History, Pencil, RotateCcw, Share2, ShieldX, Star, ThumbsUp, XCircle } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { WarningsPanel } from "@/components/memory";
import { Alert, Badge, Button, Card, Empty, Field, KeyValue, Meter, Modal, Spinner, Tabs } from "@/components/ui";
import { useAuth } from "@/hooks/useAuth";
import { useAgents, useTeams } from "@/hooks/useData";
import { ApiError, api, errorMessage } from "@/services/api";
import { MEMORY_TYPES, VISIBILITIES, type Memory, type MemoryCreateResponse, type MemoryType, type MemoryVersion, type Provenance, type Visibility } from "@/types";
import { formatDate, humanize } from "@/utils/format";

type Tab = "versions" | "provenance" | "permissions" | "audit";

export default function MemoryDetailPage() {
  const { id = "" } = useParams();
  const { isAdmin, can, me } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("versions");
  const [editing, setEditing] = useState(false);
  const [lastUpdate, setLastUpdate] = useState<MemoryCreateResponse | null>(null);

  const memory = useQuery({ queryKey: ["memory", id], queryFn: () => api.get<Memory>(`/api/memories/${id}`, { include_expired: true }), retry: false });
  const versions = useQuery({ queryKey: ["versions", id], queryFn: () => api.get<MemoryVersion[]>(`/api/memories/${id}/versions`), enabled: memory.isSuccess });
  const provenance = useQuery({ queryKey: ["provenance", id], queryFn: () => api.get<Provenance>(`/api/memories/${id}/provenance`), enabled: memory.isSuccess });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["memory", id] });
    qc.invalidateQueries({ queryKey: ["versions", id] });
    qc.invalidateQueries({ queryKey: ["provenance", id] });
  };
  const verify = useMutation({ mutationFn: (status: string) => api.post(`/api/memories/${id}/verify`, { status }), onSuccess: refresh });
  const archive = useMutation({ mutationFn: () => api.del(`/api/memories/${id}`), onSuccess: refresh });
  const restore = useMutation({ mutationFn: () => api.post(`/api/memories/${id}/restore`), onSuccess: refresh });
  const feedback = useMutation({ mutationFn: (rating: number) => api.post(`/api/memories/${id}/feedback`, { rating, helpful: rating >= 3 }), onSuccess: refresh });

  if (memory.isLoading) return <Spinner />;
  if (memory.error) {
    const denied = memory.error instanceof ApiError && memory.error.status === 403;
    return (
      <div className="stack">
        <Link to="/memories" className="row gap-sm"><ArrowLeft size={15} /> Back to explorer</Link>
        <div className="decision-banner decision-denied">
          <ShieldX size={40} />
          <div>
            <div className="decision-title">{denied ? "ACCESS DENIED" : "NOT AVAILABLE"}</div>
            <div>{errorMessage(memory.error)}</div>
            {denied && <div className="subtle" style={{ color: "inherit" }}>This attempt has been recorded in the audit log.</div>}
          </div>
        </div>
      </div>
    );
  }
  const m = memory.data!;
  const canVerify = !!me && can("memory:verify") && (isAdmin || me.team_id === m.team.id);
  const mutationError = verify.error || archive.error || restore.error || feedback.error;

  return (
    <div className="stack-lg">
      <div className="row between">
        <button className="btn btn-ghost btn-sm" onClick={() => navigate(-1)}><ArrowLeft size={15} /> Back</button>
        <div className="row gap-sm">
          {m.can_edit && m.status === "active" && <Button variant="secondary" icon={<Pencil size={15} />} onClick={() => { setLastUpdate(null); setEditing(true); }}>Edit (new version)</Button>}
          {canVerify && m.verification_status !== "verified" && <Button variant="success" icon={<CheckCircle2 size={15} />} loading={verify.isPending} onClick={() => verify.mutate("verified")}>Verify</Button>}
          {canVerify && m.verification_status !== "rejected" && <Button variant="secondary" icon={<XCircle size={15} />} onClick={() => verify.mutate("rejected")}>Reject</Button>}
          {can("memory:delete") && m.status === "active" && m.can_edit && <Button variant="danger" icon={<Archive size={15} />} loading={archive.isPending} onClick={() => archive.mutate()}>Archive</Button>}
          {isAdmin && m.status === "archived" && <Button variant="secondary" icon={<RotateCcw size={15} />} loading={restore.isPending} onClick={() => restore.mutate()}>Restore</Button>}
        </div>
      </div>
      {mutationError && <Alert tone="danger">{errorMessage(mutationError)}</Alert>}
      {lastUpdate && <WarningsPanel warnings={lastUpdate.warnings} />}

      <div className="grid grid-3">
        <Card className="span-2">
          <div className="stack">
            <div className="row gap-sm wrap">
              <Badge value={m.memory_type} />
              <Badge value={m.visibility} />
              <Badge value={m.sensitivity} />
              <Badge value={m.status} />
              <Badge value={m.verification_status} />
              {m.conflict_status !== "none" && <Badge value={m.conflict_status} />}
              {m.is_expired && <Badge tone="slate">expired</Badge>}
            </div>
            <h1 style={{ fontSize: 22 }}>{m.title}</h1>
            <div className="memory-content">{m.content}</div>
            <div className="row gap-sm wrap">{m.tags.map((t) => <span key={t} className="tag">{t}</span>)}</div>
          </div>
        </Card>
        <Card title="Metadata">
          <KeyValue
            items={[
              ["Creator", m.creator_agent ? <span>{m.creator_agent.name} <Badge tone="blue">agent</Badge></span> : m.creator_user?.name ?? "—"],
              ["Team", m.team.name],
              ["Project", m.project?.name ?? "—"],
              ["Version", `v${m.version}`],
              ["Created", formatDate(m.created_at)],
              ["Updated", formatDate(m.updated_at)],
              ["Expires", m.expires_at ? formatDate(m.expires_at) : "never"],
              ["Source", m.source_ref ? `${m.source} · ${m.source_ref}` : m.source],
              ["Confidence", <div className="row gap-sm"><Meter value={m.confidence} /> {m.confidence.toFixed(2)}</div>],
              ["Importance", <div className="row gap-sm"><Meter value={m.importance} /> {m.importance.toFixed(2)}</div>],
              ["Reliability", <div className="row gap-sm"><Meter value={m.reliability} /> {m.reliability.toFixed(2)}</div>],
              ["Retrievals", `${m.access_count} total · ${m.reuse_count} cross-team`],
              ["Embedding", m.embedding_model ?? "hidden"],
            ]}
          />
          <div className="row gap-sm" style={{ marginTop: 12 }}>
            <span className="subtle">Rate usefulness:</span>
            {[1, 2, 3, 4, 5].map((r) => (
              <button key={r} className="icon-btn" title={`${r} star${r > 1 ? "s" : ""}`} aria-label={`Rate ${r} of 5`} onClick={() => feedback.mutate(r)}>
                <Star size={16} color="#f59e0b" />
              </button>
            ))}
            {feedback.isSuccess && <ThumbsUp size={15} color="var(--green)" />}
          </div>
        </Card>
      </div>

      <Card>
        <Tabs<Tab>
          value={tab}
          onChange={setTab}
          tabs={[
            { id: "versions", label: <span className="row gap-sm"><History size={15} /> Versions ({versions.data?.length ?? "…"})</span> },
            { id: "provenance", label: "Provenance" },
            { id: "permissions", label: "Permissions & sharing" },
            { id: "audit", label: "Access history" },
          ]}
        />
        {tab === "versions" && <VersionsTab versions={versions.data} />}
        {tab === "provenance" && <ProvenanceTab p={provenance.data} />}
        {tab === "permissions" && <PermissionsTab m={m} onChange={refresh} />}
        {tab === "audit" && <AccessTab p={provenance.data} />}
      </Card>

      <EditModal key={m.version} m={m} open={editing} onClose={() => setEditing(false)} onSaved={(res) => { setLastUpdate(res); setEditing(false); refresh(); }} />
    </div>
  );
}

function VersionsTab({ versions }: { versions?: MemoryVersion[] }) {
  if (!versions) return <Spinner />;
  return (
    <div className="timeline">
      {versions.map((v) => (
        <div key={v.id} className={`timeline-item${v.is_current ? " current" : ""}`}>
          <span className="timeline-dot" />
          <div className="row gap-sm wrap">
            <strong>Version {v.version_number}</strong>
            {v.is_current && <Badge tone="blue">current</Badge>}
            <span className="muted">{v.change_description}</span>
            <span className="subtle">· {v.author.name} ({v.author.type}) · {formatDate(v.created_at)}</span>
          </div>
          <div className="version-content">
            <strong>{v.title}</strong>
            <div>{v.content}</div>
            <div className="subtle" style={{ marginTop: 6 }}>
              {humanize(v.memory_type)} · {humanize(v.visibility)} · confidence {v.confidence.toFixed(2)} · tags: {v.tags.join(", ") || "—"}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function ProvenanceTab({ p }: { p?: Provenance }) {
  if (!p) return <Spinner />;
  return (
    <div className="grid grid-2">
      <KeyValue
        items={[
          ["Created by", `${p.created_by.name} (${p.created_by.type})`],
          ["Team", p.team.name],
          ["Created", formatDate(p.created_at)],
          ["Source", p.source_ref ? `${p.source} · ${p.source_ref}` : p.source],
          ["Derived from", p.parent_memory_id ? <Link to={`/memories/${p.parent_memory_id}`}>parent memory</Link> : "—"],
          ["Versions", p.versions.map((v) => `v${v.version_number}`).join(" → ")],
          ["Modified by", p.modified_by.length ? p.modified_by.join(", ") : "not modified"],
        ]}
      />
      <KeyValue
        items={[
          ["Direct reads", p.access_summary.direct_reads],
          ["Search hits", p.access_summary.search_hits],
          ["Distinct accessors", p.access_summary.distinct_accessors],
          ["Cross-team reuse", p.access_summary.cross_team_reuse],
          ["Denied attempts", <span style={{ color: p.access_summary.denied_attempts ? "var(--red)" : undefined }}>{p.access_summary.denied_attempts}</span>],
        ]}
      />
    </div>
  );
}

function AccessTab({ p }: { p?: Provenance }) {
  if (!p) return <Spinner />;
  if (!p.access_history) return <Alert tone="info">Access history is only visible to the owning team and administrators.</Alert>;
  if (!p.access_history.length) return <Empty title="No access recorded yet" />;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Status</th><th>Reason</th></tr></thead>
        <tbody>
          {p.access_history.map((e, i) => (
            <tr key={i}>
              <td className="nowrap subtle">{formatDate(e.timestamp)}</td>
              <td>{e.actor} <span className="subtle">({e.actor_type})</span></td>
              <td><code>{e.action}</code></td>
              <td><Badge value={e.status} /></td>
              <td className="muted">{e.reason ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PermissionsTab({ m, onChange }: { m: Memory; onChange: () => void }) {
  const teams = useTeams();
  const agents = useAgents();
  const [teamId, setTeamId] = useState("");
  const [agentId, setAgentId] = useState("");
  const share = useMutation({
    mutationFn: (body: { team_ids?: string[]; agent_ids?: string[] }) => api.post(`/api/memories/${m.id}/share`, body),
    onSuccess: () => { setTeamId(""); setAgentId(""); onChange(); },
  });
  const unshare = useMutation({ mutationFn: (target: string) => api.del(`/api/memories/${m.id}/share/${target}`), onSuccess: onChange });
  const rule: Record<Visibility, string> = {
    private: "Only the creating agent can read this memory.",
    team: "All agents of the owning team, plus any explicitly shared teams/agents.",
    selected_teams: "The owning team and the explicitly allowed teams.",
    selected_agents: "Only the explicitly allowed agents (and the creator).",
    organization: "Every authenticated agent in the organisation.",
  };
  return (
    <div className="stack">
      <Alert tone="info" title={`Visibility: ${humanize(m.visibility)} · Sensitivity: ${humanize(m.sensitivity)}`}>{rule[m.visibility]} Enforced by the API and inside the vector search filter.</Alert>
      {m.allowed_teams === null ? (
        <Alert tone="warning">Sharing details are only visible to the owning team.</Alert>
      ) : (
        <div className="grid grid-2">
          <Card title="Allowed teams">
            {m.allowed_teams.length === 0 ? <span className="muted">No explicit team grants.</span> : m.allowed_teams.map((t) => (
              <div key={t.id} className="row between" style={{ padding: "6px 0" }}>
                <span>{t.name}</span>
                {m.can_share && <Button size="sm" variant="ghost" onClick={() => unshare.mutate(`team:${t.id}`)}>Revoke</Button>}
              </div>
            ))}
            {m.can_share && m.visibility !== "private" && (
              <div className="row gap-sm" style={{ marginTop: 10 }}>
                <select className="select" value={teamId} onChange={(e) => setTeamId(e.target.value)}>
                  <option value="">Share with team…</option>
                  {teams.data?.filter((t) => t.id !== m.team.id && !m.allowed_teams?.some((a) => a.id === t.id)).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
                <Button size="sm" icon={<Share2 size={14} />} disabled={!teamId} loading={share.isPending} onClick={() => share.mutate({ team_ids: [teamId] })}>Share</Button>
              </div>
            )}
          </Card>
          <Card title="Allowed agents">
            {m.allowed_agents!.length === 0 ? <span className="muted">No explicit agent grants.</span> : m.allowed_agents!.map((a) => (
              <div key={a.id} className="row between" style={{ padding: "6px 0" }}>
                <span>{a.name}</span>
                {m.can_share && <Button size="sm" variant="ghost" onClick={() => unshare.mutate(`agent:${a.id}`)}>Revoke</Button>}
              </div>
            ))}
            {m.can_share && m.visibility !== "private" && (
              <div className="row gap-sm" style={{ marginTop: 10 }}>
                <select className="select" value={agentId} onChange={(e) => setAgentId(e.target.value)}>
                  <option value="">Share with agent…</option>
                  {agents.data?.filter((a) => !m.allowed_agents?.some((x) => x.id === a.id)).map((a) => <option key={a.id} value={a.id}>{a.name} ({a.team_name})</option>)}
                </select>
                <Button size="sm" icon={<Share2 size={14} />} disabled={!agentId} loading={share.isPending} onClick={() => share.mutate({ agent_ids: [agentId] })}>Share</Button>
              </div>
            )}
          </Card>
        </div>
      )}
      {(share.error || unshare.error) && <Alert tone="danger">{errorMessage(share.error || unshare.error)}</Alert>}
    </div>
  );
}

function EditModal({ m, open, onClose, onSaved }: { m: Memory; open: boolean; onClose: () => void; onSaved: (r: MemoryCreateResponse) => void }) {
  const [title, setTitle] = useState(m.title);
  const [content, setContent] = useState(m.content);
  const [type, setType] = useState<MemoryType>(m.memory_type);
  const [visibility, setVisibility] = useState<Visibility>(m.visibility);
  const [tags, setTags] = useState(m.tags.join(", "));
  const [confidence, setConfidence] = useState(m.confidence);
  const [description, setDescription] = useState("");
  const save = useMutation({
    mutationFn: () =>
      api.patch<MemoryCreateResponse>(`/api/memories/${m.id}`, {
        title: title !== m.title ? title : undefined,
        content: content !== m.content ? content : undefined,
        memory_type: type !== m.memory_type ? type : undefined,
        visibility: visibility !== m.visibility ? visibility : undefined,
        tags: tags !== m.tags.join(", ") ? tags.split(",").map((t) => t.trim()).filter(Boolean) : undefined,
        confidence: confidence !== m.confidence ? confidence : undefined,
        change_description: description || "Updated via dashboard",
        expected_version: m.version,
      }),
    onSuccess: onSaved,
  });
  return (
    <Modal
      title={`Edit memory — creates version ${m.version + 1}`}
      open={open}
      onClose={onClose}
      wide
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button loading={save.isPending} onClick={() => save.mutate()}>Save new version</Button></>}
    >
      {save.error && <Alert tone="danger">{errorMessage(save.error)}</Alert>}
      <div className="form-grid">
        <div className="full"><Field label="Title"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></Field></div>
        <div className="full"><Field label="Content"><textarea className="textarea" value={content} onChange={(e) => setContent(e.target.value)} /></Field></div>
        <Field label="Type">
          <select className="select" value={type} onChange={(e) => setType(e.target.value as MemoryType)}>{MEMORY_TYPES.map((t) => <option key={t} value={t}>{humanize(t)}</option>)}</select>
        </Field>
        <Field label="Visibility">
          <select className="select" value={visibility} onChange={(e) => setVisibility(e.target.value as Visibility)}>{VISIBILITIES.map((t) => <option key={t} value={t}>{humanize(t)}</option>)}</select>
        </Field>
        <Field label="Tags"><input className="input" value={tags} onChange={(e) => setTags(e.target.value)} /></Field>
        <Field label={`Confidence · ${confidence.toFixed(2)}`}><input className="range" type="range" min={0} max={1} step={0.05} value={confidence} onChange={(e) => setConfidence(Number(e.target.value))} /></Field>
        <div className="full"><Field label="Change description" hint="Stored with the new version"><input className="input" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="e.g. PostgreSQL + Redis was selected" /></Field></div>
      </div>
    </Modal>
  );
}
