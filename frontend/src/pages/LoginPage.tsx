import { BrainCircuit, LockKeyhole } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Alert, Button, Field } from "@/components/ui";
import { useAuth } from "@/hooks/useAuth";
import { errorMessage } from "@/services/api";

export default function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-hero">
        <div className="brand" style={{ padding: 0 }}>
          <div className="brand-logo">
            <BrainCircuit size={20} color="#fff" />
          </div>
          <div>
            <div className="brand-name">Shared Memory Layer</div>
            <div className="brand-sub">for Cross-Team AI Agents</div>
          </div>
        </div>
        <div>
          <h1>One secure memory for every team's AI agents.</h1>
          <p>
            Agents store decisions, learnings and solutions once — and authorised agents in other teams find them by meaning,
            with every access checked and audited.
          </p>
        </div>
        <div className="hero-flow">
          <div>Agent A → <strong>Shared Memory</strong> → Agent B</div>
          <div>Semantic retrieval · RBAC + per-memory visibility · Versioning · Audit trail</div>
        </div>
      </div>
      <div className="login-panel">
        <form className="login-card" onSubmit={submit}>
          <div>
            <h2>Sign in</h2>
            <p className="muted" style={{ margin: "4px 0 0" }}>Administrators, team managers and reviewers.</p>
          </div>
          {error && <Alert tone="danger">{error}</Alert>}
          <Field label="Username">
            <input className="input" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus />
          </Field>
          <Field label="Password">
            <input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </Field>
          <Button type="submit" loading={busy} icon={<LockKeyhole size={16} />}>
            Sign in
          </Button>
          <p className="subtle">
            The bootstrap administrator is created from <code>ADMIN_USERNAME</code> / <code>ADMIN_PASSWORD</code> in <code>.env</code>.
          </p>
        </form>
      </div>
    </div>
  );
}
