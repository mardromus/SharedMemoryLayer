import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileUp, Plus, Search, Sparkles } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import ImportModal from "@/components/ImportModal";
import { MemoryForm, SearchResultCard, toMemoryPayload, WarningsPanel } from "@/components/memory";
import { Alert, Badge, Button, Card, Empty, Field, Modal, PageHeader, Pagination, Spinner, Tabs } from "@/components/ui";
import { useAuth } from "@/hooks/useAuth";
import { useProjects, useTeams } from "@/hooks/useData";
import { api, errorMessage } from "@/services/api";
import { MEMORY_TYPES, VISIBILITIES, type MemoryCreateResponse, type MemoryListItem, type MemoryType, type Page, type SearchResponse } from "@/types";
import { formatDate, humanize } from "@/utils/format";

type Mode = "semantic" | "browse";

export default function MemoryExplorerPage() {
  const { can, me, isAdmin } = useAuth();
  const teams = useTeams();
  const projects = useProjects();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [mode, setMode] = useState<Mode>("semantic");
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState({ memory_type: "", team_id: "", project_id: "", visibility: "", verification_status: "", status: "", tag: "" });
  const [topK, setTopK] = useState(10);
  const [threshold, setThreshold] = useState(0.5);
  const [includeExpired, setIncludeExpired] = useState(false);
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<MemoryCreateResponse | null>(null);
  const [importing, setImporting] = useState(false);
  const [searchMode, setSearchMode] = useState<"hybrid" | "semantic" | "keyword">("hybrid");

  const setFilter = (k: keyof typeof filters, v: string) => {
    setFilters((f) => ({ ...f, [k]: v }));
    setPage(1);
  };

  const search = useMutation({
    mutationFn: () =>
      api.post<SearchResponse>("/api/memories/search", {
        query,
        mode: searchMode,
        top_k: topK,
        similarity_threshold: threshold,
        memory_types: filters.memory_type ? [filters.memory_type as MemoryType] : undefined,
        team_ids: filters.team_id ? [filters.team_id] : undefined,
        project_ids: filters.project_id ? [filters.project_id] : undefined,
        visibility: filters.visibility ? [filters.visibility] : undefined,
        verification_status: filters.verification_status ? [filters.verification_status] : undefined,
        tags: filters.tag ? [filters.tag] : undefined,
        include_expired: includeExpired,
        include_archived: isAdmin && filters.status === "archived",
      }),
  });

  const list = useQuery({
    queryKey: ["memories", query, filters, includeExpired, page],
    queryFn: () =>
      api.get<Page<MemoryListItem>>("/api/memories", { q: query || undefined, ...filters, include_expired: includeExpired, page, page_size: 20 }),
    enabled: mode === "browse",
  });

  const create = useMutation({
    mutationFn: (payload: ReturnType<typeof toMemoryPayload>) => api.post<MemoryCreateResponse>("/api/memories", payload),
    onSuccess: (res) => {
      setCreated(res);
      qc.invalidateQueries({ queryKey: ["memories"] });
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    if (mode === "semantic" && query.trim()) search.mutate();
    else setPage(1);
  }

  return (
    <div className="stack-lg">
      <PageHeader
        title="Memory Explorer"
        description="Semantic search ranks memories by meaning; results are filtered by your permissions before they are returned."
        actions={can("memory:create") && (
          <>
            <Button variant="secondary" icon={<FileUp size={16} />} onClick={() => setImporting(true)}>Import</Button>
            <Button icon={<Plus size={16} />} onClick={() => { setCreated(null); setCreating(true); }}>New memory</Button>
          </>
        )}
      />

      <Card>
        <Tabs<Mode>
          value={mode}
          onChange={setMode}
          tabs={[
            { id: "semantic", label: <span className="row gap-sm"><Sparkles size={15} /> Semantic search</span> },
            { id: "browse", label: <span className="row gap-sm"><Search size={15} /> Browse & keyword filter</span> },
          ]}
        />
        <form className="stack" onSubmit={submit}>
          <div className="row gap search-row">
            <div className="search-box">
              <Search size={18} />
              <input
                className="input"
                aria-label={mode === "semantic" ? "Search question" : "Keyword filter"}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={mode === "semantic" ? "e.g. authentication failures caused by expired tokens" : "Keyword in title, content or tags"}
              />
            </div>
            {mode === "semantic" && (
              <div className="segmented" role="group" aria-label="Search mode">
                {(["hybrid", "semantic", "keyword"] as const).map((m) => (
                  <button key={m} type="button" aria-pressed={searchMode === m} onClick={() => setSearchMode(m)}
                    title={{ hybrid: "Meaning + exact keywords (best default)", semantic: "Meaning only (embeddings)", keyword: "Exact terms only (full-text)" }[m]}>
                    {m[0].toUpperCase() + m.slice(1)}
                  </button>
                ))}
              </div>
            )}
            <Button type="submit" loading={search.isPending} disabled={mode === "semantic" && !query.trim()}>
              {mode === "semantic" ? "Search" : "Filter"}
            </Button>
          </div>
          <div className="grid grid-4">
            <Field label="Type">
              <select className="select" value={filters.memory_type} onChange={(e) => setFilter("memory_type", e.target.value)}>
                <option value="">All types</option>
                {MEMORY_TYPES.map((t) => <option key={t} value={t}>{humanize(t)}</option>)}
              </select>
            </Field>
            <Field label="Team">
              <select className="select" value={filters.team_id} onChange={(e) => setFilter("team_id", e.target.value)}>
                <option value="">All teams</option>
                {teams.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </Field>
            <Field label="Project">
              <select className="select" value={filters.project_id} onChange={(e) => setFilter("project_id", e.target.value)}>
                <option value="">All projects</option>
                {projects.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </Field>
            <Field label="Visibility">
              <select className="select" value={filters.visibility} onChange={(e) => setFilter("visibility", e.target.value)}>
                <option value="">Any</option>
                {VISIBILITIES.map((v) => <option key={v} value={v}>{humanize(v)}</option>)}
              </select>
            </Field>
            <Field label="Verification">
              <select className="select" value={filters.verification_status} onChange={(e) => setFilter("verification_status", e.target.value)}>
                <option value="">Any</option>
                <option value="verified">Verified</option>
                <option value="unverified">Unverified</option>
                <option value="rejected">Rejected</option>
              </select>
            </Field>
            <Field label="Tag">
              <input className="input" value={filters.tag} onChange={(e) => setFilter("tag", e.target.value.toLowerCase())} placeholder="e.g. authentication" />
            </Field>
            {isAdmin && (
              <Field label="Status">
                <select className="select" value={filters.status} onChange={(e) => setFilter("status", e.target.value)}>
                  <option value="">Active + archived</option>
                  <option value="active">Active</option>
                  <option value="archived">Archived</option>
                </select>
              </Field>
            )}
            <Field label="Expired memories">
              <label className="checkbox" style={{ height: 36 }}>
                <input type="checkbox" checked={includeExpired} onChange={(e) => setIncludeExpired(e.target.checked)} /> Include expired
              </label>
            </Field>
            {mode === "semantic" && (
              <>
                <Field label={`Top-K · ${topK}`}>
                  <input className="range" type="range" min={1} max={50} value={topK} onChange={(e) => setTopK(Number(e.target.value))} />
                </Field>
                <Field label={`Similarity threshold · ${threshold.toFixed(2)}`}>
                  <input className="range" type="range" min={0} max={0.95} step={0.05} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} />
                </Field>
              </>
            )}
          </div>
        </form>
      </Card>

      {mode === "semantic" && (
        <div className="stack">
          {search.error && <Alert tone="danger">{errorMessage(search.error)}</Alert>}
          {search.data && (
            <div className="row between">
              <span className="muted">
                {search.data.total} result{search.data.total === 1 ? "" : "s"} for “{search.data.query}” · {search.data.took_ms} ms ·{" "}
                <Badge tone={search.data.mode === "semantic" ? "blue" : "amber"}>{search.data.mode}</Badge>
              </span>
              <span className="subtle">Searching as {me?.name} — only memories you are authorised to read are scored.</span>
            </div>
          )}
          {search.data?.results.map((r, i) => <SearchResultCard key={r.memory_id} r={r} rank={i + 1} />)}
          {search.data && search.data.results.length === 0 && (
            <Card><Empty title="No authorised memory is similar enough">Lower the similarity threshold or broaden the filters.</Empty></Card>
          )}
          {!search.data && !search.isPending && (
            <Card><Empty title="Ask a question in natural language" icon={<Sparkles size={26} />}>Try “Have we previously encountered authentication problems?”</Empty></Card>
          )}
        </div>
      )}

      {mode === "browse" && (
        <Card className="card-flush">
          {list.isLoading ? (
            <Spinner />
          ) : list.error ? (
            <Alert tone="danger">{errorMessage(list.error)}</Alert>
          ) : (
            <>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Title</th><th>Type</th><th>Team</th><th>Creator</th><th>Visibility</th><th>Status</th><th>Ver.</th><th>Updated</th>
                    </tr>
                  </thead>
                  <tbody>
                    {list.data?.items.map((m) => (
                      <tr key={m.id} className="clickable" onClick={() => navigate(`/memories/${m.id}`)}>
                        <td style={{ maxWidth: 380 }}>
                          <Link to={`/memories/${m.id}`} onClick={(e) => e.stopPropagation()}><strong>{m.title}</strong></Link>
                          <div className="subtle truncate">{m.snippet}</div>
                        </td>
                        <td><Badge value={m.memory_type} /></td>
                        <td>{m.team.name}</td>
                        <td className="nowrap">{m.creator}</td>
                        <td>
                          <div className="row gap-sm">
                            <Badge value={m.visibility} />
                            {m.sensitivity === "confidential" || m.sensitivity === "restricted" ? <Badge value={m.sensitivity} /> : null}
                          </div>
                        </td>
                        <td>
                          <div className="row gap-sm">
                            <Badge value={m.status} />
                            {m.verification_status !== "unverified" && <Badge value={m.verification_status} />}
                            {m.conflict_status === "potential_conflict" && <Badge value="potential_conflict">conflict</Badge>}
                            {m.expires_at && new Date(m.expires_at) < new Date() && <Badge tone="slate">expired</Badge>}
                          </div>
                        </td>
                        <td>v{m.version}</td>
                        <td className="nowrap subtle">{formatDate(m.updated_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {list.data && list.data.items.length === 0 && <Empty title="No memories match these filters" />}
              {list.data && <Pagination page={page} pageSize={20} total={list.data.total} onChange={setPage} />}
            </>
          )}
        </Card>
      )}

      <ImportModal open={importing} onClose={() => setImporting(false)} />

      <Modal title={created ? "Memory stored" : "Create memory"} open={creating} onClose={() => setCreating(false)} wide>
        {created ? (
          <div className="stack">
            <WarningsPanel warnings={created.warnings} />
            <div className="row gap-sm">
              <Button onClick={() => navigate(`/memories/${created.memory.id}`)}>Open memory</Button>
              <Button variant="secondary" onClick={() => setCreated(null)}>Create another</Button>
            </div>
          </div>
        ) : (
          <>
            {create.error && <Alert tone="danger">{errorMessage(create.error)}</Alert>}
            <MemoryForm
              busy={create.isPending}
              showTeam={isAdmin && !me?.team_id}
              ownTeamId={me?.team_id}
              onSubmit={(v) => create.mutate(toMemoryPayload(v))}
            />
          </>
        )}
      </Modal>
    </div>
  );
}
