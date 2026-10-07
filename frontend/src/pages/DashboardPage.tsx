import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Bot,
  Brain,
  CalendarPlus,
  Gauge,
  GitCompareArrows,
  Recycle,
  Search,
  ShieldAlert,
  Users,
} from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Alert, Badge, Button, Card, Empty, PageHeader, Spinner } from "@/components/ui";
import { useAuth } from "@/hooks/useAuth";
import { api, errorMessage } from "@/services/api";
import type { DashboardStats } from "@/types";
import { formatDate, humanize, pct, timeAgo } from "@/utils/format";

const PALETTE = ["#4f46e5", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#14b8a6", "#64748b", "#ec4899"];

function Stat({ label, value, hint, icon, tone }: { label: string; value: ReactNode; hint?: ReactNode; icon: ReactNode; tone: string }) {
  return (
    <div className="stat">
      <div className={`stat-icon tone-${tone}`}>{icon}</div>
      <div style={{ minWidth: 0 }}>
        <div className="stat-label">{label}</div>
        <div className="stat-value">{value}</div>
        {hint && <div className="stat-hint">{hint}</div>}
      </div>
    </div>
  );
}

function HealthRow({ label, up, detail }: { label: string; up: boolean; detail?: string }) {
  return (
    <div className="row between" style={{ padding: "8px 0", borderBottom: "1px dashed var(--border)" }}>
      <div className="row gap-sm">
        <span className={`health-dot ${up ? "health-up" : "health-down"}`} />
        <strong style={{ fontWeight: 600 }}>{label}</strong>
      </div>
      <span className="subtle">{detail ?? (up ? "operational" : "unavailable")}</span>
    </div>
  );
}

export default function DashboardPage() {
  const { isAdmin } = useAuth();
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api.get<DashboardStats>("/api/dashboard/stats"),
    refetchInterval: 15_000,
  });
  const consistency = useQuery({
    queryKey: ["consistency"],
    queryFn: () => api.get<{ postgres_memories: number; vector_points: number; missing_vectors: string[] }>("/api/admin/consistency"),
    enabled: isAdmin,
    refetchInterval: 30_000,
  });
  const reindex = useMutation({
    mutationFn: () => api.post<{ reindexed: number; seconds: number }>("/api/admin/reindex"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["consistency"] }),
  });

  if (isLoading) return <Spinner />;
  if (error || !data) return <Alert tone="danger">{errorMessage(error)}</Alert>;
  const c = data.counts;
  const h = data.health;

  return (
    <div className="stack-lg">
      <PageHeader
        title="Dashboard"
        description={`Live statistics computed from the database · updated ${timeAgo(data.generated_at)}`}
        actions={<Link className="btn btn-primary" to="/playground">Open Agent Playground</Link>}
      />

      <div className="grid grid-5">
        <Stat label="Agents" value={c.agents} hint={`${c.active_agents} active in last 24h`} icon={<Bot size={19} />} tone="blue" />
        <Stat label="Teams" value={c.teams} hint={`${c.users} human users`} icon={<Users size={19} />} tone="violet" />
        <Stat label="Memories" value={c.memories} hint={`${c.archived_memories} archived · ${c.expired_memories} expired`} icon={<Brain size={19} />} tone="teal" />
        <Stat label="Memories today" value={c.memories_today} hint={`${c.verified_memories} verified overall`} icon={<CalendarPlus size={19} />} tone="green" />
        <Stat label="Searches today" value={c.searches_today} hint={`${c.searches_total} in total`} icon={<Search size={19} />} tone="blue" />
        <Stat label="Access denials today" value={c.access_denials_today} hint={`${c.access_denials_total} in total`} icon={<ShieldAlert size={19} />} tone="red" />
        <Stat label="Cross-team memory reuse" value={c.memory_reuse} hint={`of ${c.memory_accesses} memory retrievals`} icon={<Recycle size={19} />} tone="green" />
        <Stat label="Retrieval success rate" value={pct(data.retrieval_success_rate)} hint={`cross-team hits in ${pct(data.cross_team_search_rate)} of searches`} icon={<Gauge size={19} />} tone="violet" />
        <Stat label="Avg search latency (24h)" value={data.avg_search_latency_ms_24h !== null ? `${data.avg_search_latency_ms_24h} ms` : "—"} hint="embedding + vector search + authz" icon={<Activity size={19} />} tone="amber" />
        <Stat label="Open conflicts" value={c.open_conflicts} hint={`${c.duplicates_flagged} duplicates flagged`} icon={<GitCompareArrows size={19} />} tone="amber" />
      </div>

      <div className="grid grid-3">
        <Card title="Activity — last 7 days" className="span-2">
          <div style={{ height: 260 }}>
            <ResponsiveContainer>
              <BarChart data={data.activity_7d} margin={{ top: 8, right: 16, left: -12, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                <XAxis dataKey="date" tickFormatter={(d: string) => d.slice(5)} fontSize={12} />
                <YAxis allowDecimals={false} fontSize={12} />
                <Tooltip />
                <Legend />
                <Bar dataKey="memories_created" name="Memories created" fill="#4f46e5" radius={[3, 3, 0, 0]} />
                <Bar dataKey="searches" name="Searches" fill="#0ea5e9" radius={[3, 3, 0, 0]} />
                <Bar dataKey="denials" name="Access denials" fill="#ef4444" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="System health">
          <HealthRow label="PostgreSQL" up={h.database === "up"} />
          <HealthRow label="Qdrant vector DB" up={h.vector_db === "up"} />
          <HealthRow label="Embedding model" up={h.embedding_model.loaded} detail={h.embedding_model.model} />
          <HealthRow label="LLM provider" up={h.llm.reachable} detail={`${h.llm.provider}${h.llm.model ? ` · ${h.llm.model}` : ""}`} />
          {isAdmin && consistency.data && (
            <div className="stack" style={{ marginTop: 14 }}>
              <div className="row between">
                <span className="muted">Index consistency</span>
                <Badge tone={consistency.data.missing_vectors.length ? "red" : "green"}>
                  {consistency.data.postgres_memories} rows / {consistency.data.vector_points} vectors
                </Badge>
              </div>
              <Button variant="secondary" size="sm" loading={reindex.isPending} onClick={() => reindex.mutate()}>
                Rebuild vector index
              </Button>
              {reindex.data && <span className="subtle">Re-indexed {reindex.data.reindexed} memories in {reindex.data.seconds}s</span>}
            </div>
          )}
        </Card>
      </div>

      <div className="grid grid-2">
        <Card title="Memories by type">
          <div style={{ height: 240 }}>
            <ResponsiveContainer>
              <BarChart data={data.memories_by_type.map((d) => ({ ...d, label: humanize(d.memory_type) }))} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="label" fontSize={11} interval={0} angle={-20} textAnchor="end" height={50} />
                <YAxis allowDecimals={false} fontSize={12} />
                <Tooltip />
                <Bar dataKey="count" name="Memories" radius={[4, 4, 0, 0]}>
                  {data.memories_by_type.map((_, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Memories by team">
          <div style={{ height: 240 }}>
            <ResponsiveContainer>
              <BarChart data={data.memories_by_team} layout="vertical" margin={{ top: 8, right: 16, left: 20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis type="number" allowDecimals={false} fontSize={12} />
                <YAxis type="category" dataKey="team" fontSize={12} width={90} />
                <Tooltip />
                <Bar dataKey="count" name="Memories" fill="#4f46e5" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      <div className="grid grid-2">
        <Card title="Most reused memories" subtitle="Retrievals by agents outside the owning team">
          {data.top_reused_memories.length === 0 ? (
            <Empty title="No retrievals yet">Run a search in the Agent Playground.</Empty>
          ) : (
            <table className="table">
              <thead>
                <tr><th>Memory</th><th>Team</th><th>Cross-team</th><th>Total</th></tr>
              </thead>
              <tbody>
                {data.top_reused_memories.map((m) => (
                  <tr key={m.id}>
                    <td><Link to={`/memories/${m.id}`}>{m.title}</Link></td>
                    <td>{m.team}</td>
                    <td><strong>{m.reuse_count}</strong></td>
                    <td>{m.access_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card title="Recent activity" actions={<Link to="/audit" className="subtle">View audit log →</Link>}>
          <div className="table-wrap">
            <table className="table">
              <tbody>
                {data.recent_activity.map((e, i) => (
                  <tr key={i}>
                    <td className="nowrap subtle">{formatDate(e.timestamp)}</td>
                    <td className="truncate" style={{ maxWidth: 140 }}>{e.actor}</td>
                    <td><code>{e.action}</code></td>
                    <td><Badge value={e.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </div>
  );
}
