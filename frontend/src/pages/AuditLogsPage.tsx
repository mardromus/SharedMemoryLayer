import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { Alert, Badge, Button, Card, Empty, Field, PageHeader, Pagination, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/services/api";
import type { AuditLog, Page } from "@/types";
import { formatDate } from "@/utils/format";

const PAGE_SIZE = 50;

export default function AuditLogsPage() {
  const [filters, setFilters] = useState({ actor: "", action: "", status: "", actor_type: "", resource_type: "", since: "", until: "" });
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<string | null>(null);
  const [live, setLive] = useState(true);
  const actions = useQuery({ queryKey: ["audit-actions"], queryFn: () => api.get<string[]>("/api/audit-logs/actions") });
  const logs = useQuery({
    queryKey: ["audit", filters, page],
    queryFn: () =>
      api.get<Page<AuditLog>>("/api/audit-logs", {
        ...filters,
        since: filters.since ? new Date(filters.since).toISOString() : undefined,
        until: filters.until ? new Date(filters.until).toISOString() : undefined,
        page,
        page_size: PAGE_SIZE,
      }),
    refetchInterval: live ? 5000 : false,
  });
  const set = (k: keyof typeof filters, v: string) => {
    setFilters((f) => ({ ...f, [k]: v }));
    setPage(1);
  };

  return (
    <div className="stack-lg">
      <PageHeader
        title="Audit Logs"
        description="Append-only record of security-relevant operations (the database rejects UPDATE/DELETE on this table)."
        actions={
          <>
            <label className="checkbox"><input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> Live</label>
            <Button variant="secondary" icon={<RefreshCw size={15} />} onClick={() => logs.refetch()}>Refresh</Button>
          </>
        }
      />
      <Card>
        <div className="grid grid-4">
          <Field label="Actor"><input className="input" value={filters.actor} onChange={(e) => set("actor", e.target.value)} placeholder="e.g. Support-Agent" /></Field>
          <Field label="Action">
            <select className="select" value={filters.action} onChange={(e) => set("action", e.target.value)}>
              <option value="">All actions</option>
              {actions.data?.map((a) => <option key={a}>{a}</option>)}
            </select>
          </Field>
          <Field label="Status">
            <select className="select" value={filters.status} onChange={(e) => set("status", e.target.value)}>
              <option value="">Any</option>
              {["success", "allowed", "denied", "failure"].map((s) => <option key={s} value={s}>{s.toUpperCase()}</option>)}
            </select>
          </Field>
          <Field label="Actor type">
            <select className="select" value={filters.actor_type} onChange={(e) => set("actor_type", e.target.value)}>
              <option value="">Any</option>
              {["user", "agent", "system", "anonymous"].map((s) => <option key={s}>{s}</option>)}
            </select>
          </Field>
          <Field label="Resource type">
            <select className="select" value={filters.resource_type} onChange={(e) => set("resource_type", e.target.value)}>
              <option value="">Any</option>
              {["memory", "search", "agent", "team", "user", "role", "conflict", "project", "agent_task"].map((s) => <option key={s}>{s}</option>)}
            </select>
          </Field>
          <Field label="From"><input className="input" type="datetime-local" value={filters.since} onChange={(e) => set("since", e.target.value)} /></Field>
          <Field label="To"><input className="input" type="datetime-local" value={filters.until} onChange={(e) => set("until", e.target.value)} /></Field>
          <Field label="Quick filters">
            <div className="row gap-sm">
              <Button size="sm" variant="secondary" onClick={() => set("status", "denied")}>Denied only</Button>
              <Button size="sm" variant="ghost" onClick={() => { setFilters({ actor: "", action: "", status: "", actor_type: "", resource_type: "", since: "", until: "" }); setPage(1); }}>Clear</Button>
            </div>
          </Field>
        </div>
      </Card>

      <Card className="card-flush">
        {logs.isLoading ? <Spinner /> : logs.error ? <Alert tone="danger">{errorMessage(logs.error)}</Alert> : !logs.data?.items.length ? <Empty title="No audit events match" /> : (
          <>
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th /><th>Timestamp</th><th>Actor</th><th>Action</th><th>Resource</th><th>Status</th><th>Reason</th></tr></thead>
                <tbody>
                  {logs.data.items.map((e) => (
                    <Fragment key={e.id}>
                      <tr className="clickable" onClick={() => setOpen(open === e.id ? null : e.id)}>
                        <td style={{ width: 28 }}>{open === e.id ? <ChevronDown size={15} /> : <ChevronRight size={15} />}</td>
                        <td className="nowrap subtle">{formatDate(e.timestamp)}</td>
                        <td>
                          <strong>{e.actor_name}</strong> <span className="subtle">{e.actor_type}</span>
                          {e.on_behalf_of && <div className="subtle">simulated by {e.on_behalf_of}</div>}
                        </td>
                        <td><code>{e.action}</code></td>
                        <td>
                          {e.resource_type === "memory" && e.resource_id ? (
                            <Link to={`/memories/${e.resource_id}`} onClick={(ev) => ev.stopPropagation()}>memory #{e.resource_id.slice(0, 8)}</Link>
                          ) : e.resource_type ? <span className="muted">{e.resource_type}{e.resource_id ? ` #${e.resource_id.slice(0, 8)}` : ""}</span> : "—"}
                        </td>
                        <td><Badge value={e.status}>{e.status.toUpperCase()}</Badge></td>
                        <td className="muted" style={{ maxWidth: 320 }}>{e.reason ?? ""}</td>
                      </tr>
                      {open === e.id && (
                        <tr>
                          <td colSpan={7} style={{ background: "var(--surface-2)" }}>
                            <div className="grid grid-2">
                              <div className="subtle">
                                <div>Event ID: <code>{e.id}</code></div>
                                <div>Request ID: <code>{e.request_id ?? "—"}</code></div>
                                <div>IP address: <code>{e.ip_address ?? "—"}</code></div>
                                <div>Actor ID: <code>{e.actor_id ?? "—"}</code></div>
                              </div>
                              <pre className="json">{JSON.stringify(e.metadata, null, 2)}</pre>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination page={page} pageSize={PAGE_SIZE} total={logs.data.total} onChange={setPage} />
          </>
        )}
      </Card>
    </div>
  );
}
