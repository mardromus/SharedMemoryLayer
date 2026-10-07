import { Copy, GitCompareArrows, Lock, Users } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Alert, Badge, Button, Field, Meter } from "@/components/ui";
import { useProjects, useTeams } from "@/hooks/useData";
import {
  MEMORY_TYPES,
  SENSITIVITIES,
  VISIBILITIES,
  type MemoryType,
  type MemoryWarnings,
  type SearchResult,
  type Sensitivity,
  type Visibility,
} from "@/types";
import { formatDate, humanize } from "@/utils/format";

export interface MemoryFormValues {
  title: string;
  content: string;
  memory_type: MemoryType;
  visibility: Visibility | "";
  sensitivity: Sensitivity;
  tags: string;
  project_id: string;
  importance: number;
  confidence: number;
  source_ref: string;
  expires_at: string;
  allowed_team_ids: string[];
  team_id?: string;
}

export const emptyMemoryForm: MemoryFormValues = {
  title: "",
  content: "",
  memory_type: "knowledge",
  visibility: "",
  sensitivity: "internal",
  tags: "",
  project_id: "",
  importance: 0.5,
  confidence: 0.8,
  source_ref: "",
  expires_at: "",
  allowed_team_ids: [],
};

export function toMemoryPayload(v: MemoryFormValues) {
  return {
    title: v.title.trim(),
    content: v.content.trim(),
    memory_type: v.memory_type,
    visibility: v.visibility || undefined,
    sensitivity: v.sensitivity,
    tags: v.tags.split(",").map((t) => t.trim()).filter(Boolean),
    project_id: v.project_id || undefined,
    importance: v.importance,
    confidence: v.confidence,
    source_ref: v.source_ref.trim() || undefined,
    expires_at: v.expires_at ? new Date(v.expires_at).toISOString() : undefined,
    allowed_team_ids: v.allowed_team_ids.length ? v.allowed_team_ids : undefined,
    team_id: v.team_id || undefined,
  };
}

export function MemoryForm({
  onSubmit,
  busy,
  submitLabel = "Store memory",
  showTeam,
  ownTeamId,
}: {
  onSubmit: (values: MemoryFormValues) => void;
  busy?: boolean;
  submitLabel?: string;
  showTeam?: boolean;
  ownTeamId?: string | null;
}) {
  const [v, setV] = useState<MemoryFormValues>(emptyMemoryForm);
  const teams = useTeams();
  const projects = useProjects();
  const set = <K extends keyof MemoryFormValues>(k: K, value: MemoryFormValues[K]) => setV((prev) => ({ ...prev, [k]: value }));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit(v);
  };
  const otherTeams = (teams.data ?? []).filter((t) => t.id !== (v.team_id || ownTeamId));

  return (
    <form className="form-grid" onSubmit={submit}>
      <Field label="Title">
        <input className="input" value={v.title} onChange={(e) => set("title", e.target.value)} required minLength={3} maxLength={200} placeholder="JWT authentication issue" />
      </Field>
      <Field label="Memory type">
        <select className="select" value={v.memory_type} onChange={(e) => set("memory_type", e.target.value as MemoryType)}>
          {MEMORY_TYPES.map((t) => <option key={t} value={t}>{humanize(t)}</option>)}
        </select>
      </Field>
      <div className="full">
        <Field label="Content">
          <textarea className="textarea" value={v.content} onChange={(e) => set("content", e.target.value)} required placeholder="Authentication failed because refresh tokens expired…" />
        </Field>
      </div>
      {showTeam && (
        <Field label="Owning team">
          <select className="select" value={v.team_id ?? ""} onChange={(e) => set("team_id", e.target.value)} required>
            <option value="">Select team…</option>
            {teams.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </Field>
      )}
      <Field label="Visibility" hint="Defaults to the owning team's default">
        <select className="select" value={v.visibility} onChange={(e) => set("visibility", e.target.value as Visibility | "")}>
          <option value="">Team default</option>
          {VISIBILITIES.map((t) => <option key={t} value={t}>{humanize(t)}</option>)}
        </select>
      </Field>
      <Field label="Sensitivity">
        <select className="select" value={v.sensitivity} onChange={(e) => set("sensitivity", e.target.value as Sensitivity)}>
          {SENSITIVITIES.map((t) => <option key={t} value={t}>{humanize(t)}</option>)}
        </select>
      </Field>
      <Field label="Project">
        <select className="select" value={v.project_id} onChange={(e) => set("project_id", e.target.value)}>
          <option value="">None</option>
          {projects.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </Field>
      <Field label="Tags" hint="Comma separated, e.g. authentication, jwt">
        <input className="input" value={v.tags} onChange={(e) => set("tags", e.target.value)} />
      </Field>
      {(v.visibility === "selected_teams" || v.visibility === "team") && (
        <div className="full">
          <Field label="Share with teams" hint="Explicit sharing grants (optional)">
            <div className="row wrap gap">
              {otherTeams.map((t) => (
                <label key={t.id} className="checkbox">
                  <input
                    type="checkbox"
                    checked={v.allowed_team_ids.includes(t.id)}
                    onChange={(e) => set("allowed_team_ids", e.target.checked ? [...v.allowed_team_ids, t.id] : v.allowed_team_ids.filter((x) => x !== t.id))}
                  />
                  {t.name}
                </label>
              ))}
            </div>
          </Field>
        </div>
      )}
      <Field label={`Importance · ${v.importance.toFixed(2)}`}>
        <input className="range" type="range" min={0} max={1} step={0.05} value={v.importance} onChange={(e) => set("importance", Number(e.target.value))} />
      </Field>
      <Field label={`Confidence · ${v.confidence.toFixed(2)}`}>
        <input className="range" type="range" min={0} max={1} step={0.05} value={v.confidence} onChange={(e) => set("confidence", Number(e.target.value))} />
      </Field>
      <Field label="Source reference" hint="e.g. Agent task #1287">
        <input className="input" value={v.source_ref} onChange={(e) => set("source_ref", e.target.value)} maxLength={255} />
      </Field>
      <Field label="Expires at (optional)">
        <input className="input" type="datetime-local" value={v.expires_at} onChange={(e) => set("expires_at", e.target.value)} />
      </Field>
      <div className="full row between">
        <span className="subtle">The server embeds the text, checks for duplicates/conflicts, versions and audits the write.</span>
        <Button type="submit" loading={busy}>{submitLabel}</Button>
      </div>
    </form>
  );
}

export function WarningsPanel({ warnings }: { warnings: MemoryWarnings | null | undefined }) {
  if (!warnings || (!warnings.message && !warnings.duplicates.length && !warnings.conflicts.length)) {
    return <Alert tone="success" title="Stored without warnings">No duplicates or conflicts were detected.</Alert>;
  }
  return (
    <div className="stack">
      {warnings.duplicates.length > 0 && (
        <Alert tone="warning" title="Potential duplicate memories found.">
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {warnings.duplicates.map((d) => (
              <li key={d.memory_id}>
                <Link to={`/memories/${d.memory_id}`}>{d.title}</Link> · {d.team} · similarity {d.similarity.toFixed(3)}
                {d.exact_match && " · exact match"}
              </li>
            ))}
          </ul>
        </Alert>
      )}
      {(warnings.conflicts.length > 0 || warnings.message?.includes("conflict")) && (
        <Alert tone="danger" title="Potential conflict detected — flagged for review">
          {warnings.conflicts.length > 0 ? (
            <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
              {warnings.conflicts.map((c) => (
                <li key={c.memory_id}>
                  <Link to={`/memories/${c.memory_id}`}>{c.title}</Link>: {c.reason} <span className="subtle">({c.detection_method}, similarity {c.similarity.toFixed(3)})</span>
                </li>
              ))}
            </ul>
          ) : (
            warnings.message
          )}
        </Alert>
      )}
    </div>
  );
}

export function SearchResultCard({ r, rank }: { r: SearchResult; rank: number }) {
  return (
    <div className="result">
      <div className="row between gap" style={{ alignItems: "flex-start" }}>
        <div style={{ minWidth: 0 }}>
          <div className="row gap-sm wrap">
            <span className="subtle">#{rank}</span>
            <Link className="result-title" to={`/memories/${r.memory_id}`}>{r.title}</Link>
            {r.cross_team && <Badge tone="violet"><Users size={11} aria-hidden="true" /> cross-team</Badge>}
            {r.match && r.match !== "semantic" && <Badge tone="teal" title="Every query term appears in this memory (full-text match)">keyword match</Badge>}
          </div>
          <p className="result-content">{r.content}</p>
          <div className="row gap-sm wrap">
            <Badge value={r.memory_type} />
            <Badge value={r.visibility} />
            {r.verification_status === "verified" && <Badge value="verified" />}
            <span className="subtle">{r.team} · {r.creator} · v{r.version} · {formatDate(r.updated_at)}</span>
            {r.tags.map((t) => <span className="tag" key={t}>{t}</span>)}
          </div>
        </div>
        <div className="similarity">
          <span className="similarity-value">{r.similarity.toFixed(3)}</span>
          <span className="subtle">similarity</span>
          <div style={{ width: 100 }}><Meter value={r.similarity} label="similarity" /></div>
          <span className="subtle">score {r.score.toFixed(3)} · conf {r.confidence.toFixed(2)}</span>
        </div>
      </div>
    </div>
  );
}

export function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="icon-btn"
      style={{ color: "#cbd5e1" }}
      title="Copy"
      aria-label={copied ? "Copied" : "Copy to clipboard"}
      onClick={() => {
        navigator.clipboard?.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? "✓" : <Copy size={15} />}
    </button>
  );
}

export function VisibilityIcon({ visibility }: { visibility: Visibility }) {
  return visibility === "private" ? <Lock size={13} /> : visibility === "organization" ? <Users size={13} /> : <GitCompareArrows size={13} />;
}
