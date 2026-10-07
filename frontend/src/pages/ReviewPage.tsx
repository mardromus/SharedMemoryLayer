import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, GitCompareArrows, XCircle } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Alert, Badge, Button, Card, Empty, PageHeader, Spinner, Tabs } from "@/components/ui";
import { useAuth } from "@/hooks/useAuth";
import { api, errorMessage } from "@/services/api";
import type { Conflict, MemoryListItem, Page } from "@/types";
import { formatDate } from "@/utils/format";

type Tab = "conflicts" | "verification" | "closed";

export default function ReviewPage() {
  const [tab, setTab] = useState<Tab>("conflicts");
  return (
    <div className="stack-lg">
      <PageHeader
        title="Review & Conflicts"
        description="Contradicting memories are never silently overwritten — they are flagged here for a human decision. Reviewers can also verify or reject memories."
      />
      <Card>
        <Tabs<Tab>
          value={tab}
          onChange={setTab}
          tabs={[
            { id: "conflicts", label: <span className="row gap-sm"><GitCompareArrows size={15} /> Open conflicts</span> },
            { id: "verification", label: <span className="row gap-sm"><CheckCircle2 size={15} /> Verification queue</span> },
            { id: "closed", label: "Resolved" },
          ]}
        />
        {tab === "conflicts" && <Conflicts status="open" />}
        {tab === "closed" && <Conflicts status="resolved" />}
        {tab === "verification" && <VerificationQueue />}
      </Card>
    </div>
  );
}

function Conflicts({ status }: { status: "open" | "resolved" }) {
  const qc = useQueryClient();
  const conflicts = useQuery({ queryKey: ["conflicts", status], queryFn: () => api.get<Conflict[]>("/api/conflicts", { status }) });
  const resolve = useMutation({
    mutationFn: ({ id, resolution }: { id: string; resolution: string }) => api.post(`/api/conflicts/${id}/resolve`, { resolution }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conflicts"] }),
  });
  if (conflicts.isLoading) return <Spinner />;
  if (!conflicts.data?.length) return <Empty title={status === "open" ? "No open conflicts" : "No resolved conflicts yet"} />;
  return (
    <div className="stack">
      {resolve.error && <Alert tone="danger">{errorMessage(resolve.error)}</Alert>}
      {conflicts.data.map((c) => (
        <div key={c.id} className="card">
          <div className="card-header">
            <div className="stack" style={{ gap: 2 }}>
              <strong>{c.reason}</strong>
              <span className="subtle">detected by <code>{c.detection_method}</code> · semantic similarity {c.similarity.toFixed(3)} · {formatDate(c.created_at)}</span>
            </div>
            {c.status === "open" ? <Badge value="open">Needs review</Badge> : <Badge value="resolved">{c.resolution?.replace("_", " ")}</Badge>}
          </div>
          <div className="card-body grid grid-2">
            <div className="stack" style={{ gap: 6 }}>
              <span className="subtle">NEW · {c.memory_team}</span>
              <Link to={`/memories/${c.memory.id}`}><strong>{c.memory.name}</strong></Link>
              <div className="version-content">{c.memory_content}</div>
            </div>
            <div className="stack" style={{ gap: 6 }}>
              <span className="subtle">EXISTING · {c.conflicting_team}</span>
              <Link to={`/memories/${c.conflicting_memory.id}`}><strong>{c.conflicting_memory.name}</strong></Link>
              <div className="version-content">{c.conflicting_content}</div>
            </div>
          </div>
          {c.status === "open" && (
            <footer className="modal-footer" style={{ borderRadius: "0 0 10px 10px" }}>
              <Button size="sm" variant="secondary" onClick={() => resolve.mutate({ id: c.id, resolution: "dismiss" })}>Not a conflict</Button>
              <Button size="sm" variant="secondary" onClick={() => resolve.mutate({ id: c.id, resolution: "keep_both" })}>Keep both</Button>
              <Button size="sm" variant="secondary" onClick={() => resolve.mutate({ id: c.id, resolution: "keep_existing" })}>Keep existing (archive new)</Button>
              <Button size="sm" onClick={() => resolve.mutate({ id: c.id, resolution: "keep_new" })}>Keep new (archive existing)</Button>
            </footer>
          )}
        </div>
      ))}
    </div>
  );
}

function VerificationQueue() {
  const { isAdmin, me } = useAuth();
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["verification-queue"],
    queryFn: () => api.get<Page<MemoryListItem>>("/api/memories", { verification_status: "unverified", status: "active", page_size: 50, team_id: isAdmin ? undefined : me?.team_id ?? undefined }),
  });
  const verify = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => api.post(`/api/memories/${id}/verify`, { status }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["verification-queue"] }),
  });
  if (list.isLoading) return <Spinner />;
  if (!list.data?.items.length) return <Empty title="Nothing waiting for verification" />;
  return (
    <div className="stack">
      {verify.error && <Alert tone="danger">{errorMessage(verify.error)}</Alert>}
      <table className="table">
        <thead><tr><th>Memory</th><th>Team</th><th>Creator</th><th>Confidence</th><th /></tr></thead>
        <tbody>
          {list.data.items.map((m) => (
            <tr key={m.id}>
              <td style={{ maxWidth: 460 }}><Link to={`/memories/${m.id}`}><strong>{m.title}</strong></Link><div className="subtle truncate">{m.snippet}</div></td>
              <td>{m.team.name}</td>
              <td>{m.creator}</td>
              <td>{m.confidence.toFixed(2)}</td>
              <td className="nowrap">
                <div className="row gap-sm">
                  <Button size="sm" variant="success" icon={<CheckCircle2 size={14} />} onClick={() => verify.mutate({ id: m.id, status: "verified" })}>Verify</Button>
                  <Button size="sm" variant="secondary" icon={<XCircle size={14} />} onClick={() => verify.mutate({ id: m.id, status: "rejected" })}>Reject</Button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
