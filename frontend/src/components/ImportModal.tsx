import { useQueryClient } from "@tanstack/react-query";
import { FileUp, Trash2 } from "lucide-react";
import { useState, type DragEvent } from "react";
import { Alert, Badge, Button, Field, Modal } from "@/components/ui";
import { useAuth } from "@/hooks/useAuth";
import { useTeams } from "@/hooks/useData";
import { api, errorMessage } from "@/services/api";
import { MEMORY_TYPES, VISIBILITIES } from "@/types";
import { humanize } from "@/utils/format";
import { fromMarkdown, normaliseTag, parseFile, type ImportItem } from "@/utils/importers";

interface BulkResult {
  created: number;
  skipped: number;
  failed: number;
  results: { index: number; status: string; title: string | null; error?: { message: string } | null }[];
}

export default function ImportModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { me, isAdmin } = useAuth();
  const teams = useTeams();
  const qc = useQueryClient();
  const [items, setItems] = useState<ImportItem[]>([]);
  const [paste, setPaste] = useState("");
  const [split, setSplit] = useState<"heading" | "file">("heading");
  const [memoryType, setMemoryType] = useState("documentation");
  const [tags, setTags] = useState("");
  const [visibility, setVisibility] = useState("");
  const [teamId, setTeamId] = useState("");
  const [skipDuplicates, setSkipDuplicates] = useState(true);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BulkResult | null>(null);
  const [dragging, setDragging] = useState(false);

  const opts = () => ({ split, memoryType, tags: tags.split(",").map(normaliseTag).filter(Boolean) });
  const needsTeam = isAdmin && !me?.team_id;

  async function addFiles(files: FileList | File[]) {
    setError(null);
    try {
      const parsed = (await Promise.all([...files].map((f) => parseFile(f, opts())))).flat();
      if (!parsed.length) setError("No importable content found (use .md, .txt, .json or .csv; sections need at least 40 characters).");
      setItems((prev) => [...prev, ...parsed]);
    } catch (e) {
      setError(`Could not read file: ${errorMessage(e)}`);
    }
  }

  function addPaste() {
    const parsed = fromMarkdown(paste, "pasted.md", opts());
    if (!parsed.length) return setError("The pasted text is too short to import.");
    setItems((prev) => [...prev, ...parsed]);
    setPaste("");
  }

  async function submit() {
    setBusy(true);
    setError(null);
    setProgress(0);
    const total: BulkResult = { created: 0, skipped: 0, failed: 0, results: [] };
    const payload = items.map((it) => ({ ...it, visibility: visibility || undefined, team_id: needsTeam ? teamId : undefined }));
    try {
      for (let start = 0; start < payload.length; start += 100) {
        const res = await api.post<BulkResult>("/api/memories/bulk", { items: payload.slice(start, start + 100), skip_duplicates: skipDuplicates });
        total.created += res.created;
        total.skipped += res.skipped;
        total.failed += res.failed;
        total.results.push(...res.results.map((r) => ({ ...r, index: r.index + start })));
        setProgress(Math.min(start + 100, payload.length));
      }
      setResult(total);
      setItems([]);
      qc.invalidateQueries({ queryKey: ["memories"] });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  function close() {
    setResult(null);
    setError(null);
    onClose();
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  };

  return (
    <Modal
      title="Import existing knowledge"
      open={open}
      onClose={close}
      wide
      footer={
        result ? <Button onClick={close}>Done</Button> : (
          <>
            <Button variant="secondary" onClick={close}>Cancel</Button>
            <Button loading={busy} disabled={!items.length || (needsTeam && !teamId)} onClick={submit}>
              {busy ? `Importing ${progress}/${items.length}…` : `Import ${items.length} memor${items.length === 1 ? "y" : "ies"}`}
            </Button>
          </>
        )
      }
    >
      {result ? (
        <div className="stack">
          <Alert tone={result.failed ? "warning" : "success"} title={`Imported ${result.created} · skipped ${result.skipped} duplicates · ${result.failed} failed`}>
            Every item went through validation, embedding, duplicate detection and the audit log.
          </Alert>
          {result.results.filter((r) => r.status === "error").map((r) => (
            <div key={r.index} className="subtle">✗ {r.title ?? `item ${r.index + 1}`}: {r.error?.message}</div>
          ))}
        </div>
      ) : (
        <>
          {error && <Alert tone="danger">{error}</Alert>}
          <div
            className={`dropzone${dragging ? " dropzone-active" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
          >
            <FileUp size={26} aria-hidden="true" />
            <div><strong>Drop Markdown, text, JSON or CSV files here</strong></div>
            <label className="btn btn-secondary btn-sm">
              Choose files
              <input type="file" multiple accept=".md,.markdown,.txt,.json,.csv" className="visually-hidden" onChange={(e) => e.target.files && addFiles(e.target.files)} />
            </label>
            <span className="subtle">Markdown is split into one memory per “## section”. JSON/CSV need title and content fields.</span>
          </div>
          <Field label="…or paste Markdown">
            <textarea className="textarea" style={{ minHeight: 80 }} value={paste} onChange={(e) => setPaste(e.target.value)} placeholder={"# Payments runbook\n\n## Gateway timeouts\nCheck the provider status page, then fail over…"} />
          </Field>
          <div className="row gap-sm"><Button size="sm" variant="secondary" disabled={!paste.trim()} onClick={addPaste}>Add pasted text</Button></div>
          <div className="form-grid">
            <Field label="Split Markdown">
              <select className="select" value={split} onChange={(e) => setSplit(e.target.value as "heading" | "file")}>
                <option value="heading">One memory per ## section</option>
                <option value="file">One memory per file</option>
              </select>
            </Field>
            <Field label="Memory type (default)">
              <select className="select" value={memoryType} onChange={(e) => setMemoryType(e.target.value)}>
                {MEMORY_TYPES.map((t) => <option key={t} value={t}>{humanize(t)}</option>)}
              </select>
            </Field>
            <Field label="Tags for every item" hint="Comma separated">
              <input className="input" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="runbook, payments" />
            </Field>
            <Field label="Visibility">
              <select className="select" value={visibility} onChange={(e) => setVisibility(e.target.value)}>
                <option value="">Team default</option>
                {VISIBILITIES.map((v) => <option key={v} value={v}>{humanize(v)}</option>)}
              </select>
            </Field>
            {needsTeam && (
              <Field label="Owning team">
                <select className="select" value={teamId} onChange={(e) => setTeamId(e.target.value)}>
                  <option value="">Select team…</option>
                  {teams.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
              </Field>
            )}
            <Field label="Re-imports">
              <label className="checkbox" style={{ height: 36 }}>
                <input type="checkbox" checked={skipDuplicates} onChange={(e) => setSkipDuplicates(e.target.checked)} /> Skip items that already exist
              </label>
            </Field>
          </div>
          {items.length > 0 && (
            <div className="table-wrap" style={{ maxHeight: 220, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
              <table className="table">
                <caption className="visually-hidden">Items to import</caption>
                <thead><tr><th>Title</th><th>Type</th><th>Size</th><th><span className="visually-hidden">Remove</span></th></tr></thead>
                <tbody>
                  {items.map((it, i) => (
                    <tr key={i}>
                      <td>{it.title}<div className="subtle">{it.source_ref}</div></td>
                      <td><Badge value={it.memory_type} /></td>
                      <td className="subtle nowrap">{it.content.length.toLocaleString()} chars</td>
                      <td>
                        <button className="icon-btn" aria-label={`Remove ${it.title}`} onClick={() => setItems(items.filter((_, j) => j !== i))}>
                          <Trash2 size={15} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </Modal>
  );
}
