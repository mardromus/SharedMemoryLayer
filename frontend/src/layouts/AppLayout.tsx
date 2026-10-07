import {
  Bot,
  BrainCircuit,
  FlaskConical,
  GitCompareArrows,
  LayoutDashboard,
  LogOut,
  Menu,
  Plug,
  ScrollText,
  Search,
  ShieldCheck,
  Users,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Badge } from "@/components/ui";
import { useAuth } from "@/hooks/useAuth";

const NAV = [
  { section: "Overview" },
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/memories", label: "Memory Explorer", icon: Search },
  { to: "/playground", label: "Agent Playground", icon: FlaskConical },
  { section: "Organisation" },
  { to: "/agents", label: "Agents", icon: Bot },
  { to: "/connect", label: "Connect an agent", icon: Plug, permission: "agent:update" },
  { to: "/teams", label: "Teams & Projects", icon: Users },
  { to: "/access", label: "Access Control", icon: ShieldCheck, permission: "user:manage" },
  { section: "Governance" },
  { to: "/review", label: "Review & Conflicts", icon: GitCompareArrows, permission: "conflict:resolve" },
  { to: "/audit", label: "Audit Logs", icon: ScrollText, permission: "audit:read" },
] as const;

export default function AppLayout() {
  const { me, logout, can } = useAuth();
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setNavOpen(false), [location.pathname]);
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setNavOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navOpen]);

  const initials = (me?.full_name || me?.name || "?")
    .split(/[\s.]+/)
    .map((p) => p[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      {navOpen && <div className="nav-backdrop" onClick={() => setNavOpen(false)} aria-hidden="true" />}
      <aside className={`sidebar${navOpen ? " sidebar-open" : ""}`} id="primary-navigation">
        <div className="brand">
          <div className="brand-logo" aria-hidden="true">
            <BrainCircuit size={20} color="#fff" />
          </div>
          <div className="grow">
            <div className="brand-name">Shared Memory</div>
            <div className="brand-sub">Cross-team AI agents</div>
          </div>
          <button className="icon-btn nav-close" onClick={() => setNavOpen(false)} aria-label="Close navigation">
            <X size={18} />
          </button>
        </div>
        <nav aria-label="Main">
          {NAV.map((item, i) => {
            if ("section" in item) return <div key={i} className="nav-section" aria-hidden="true">{item.section}</div>;
            if ("permission" in item && item.permission && !can(item.permission)) return null;
            const Icon = item.icon;
            return (
              <NavLink key={item.to} to={item.to} end={"end" in item ? item.end : false} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
                <Icon size={17} aria-hidden="true" /> {item.label}
              </NavLink>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <div className="user-chip">
            <div className="avatar" aria-hidden="true">{initials}</div>
            <div className="grow" style={{ minWidth: 0 }}>
              <div className="truncate" style={{ fontWeight: 600 }}>{me?.full_name || me?.name}</div>
              <div className="truncate" style={{ fontSize: 12.5, color: "#aab4c8" }}>{me?.team_name ?? "No team"}</div>
            </div>
            <button className="icon-btn" style={{ color: "#cbd5e1" }} onClick={logout} title="Sign out" aria-label="Sign out">
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="row gap-sm" style={{ minWidth: 0 }}>
            <button
              className="icon-btn nav-toggle"
              onClick={() => setNavOpen(true)}
              aria-label="Open navigation"
              aria-controls="primary-navigation"
              aria-expanded={navOpen}
            >
              <Menu size={20} />
            </button>
            <span className="muted truncate">Secure organisational memory for autonomous agents</span>
          </div>
          <div className="row gap-sm">
            {me?.roles.map((r) => <Badge key={r} value={r}>{r.replace("_", " ")}</Badge>)}
            <a className="btn btn-ghost btn-sm" href="/docs" target="_blank" rel="noreferrer">API docs</a>
          </div>
        </header>
        <main className="content" id="main-content" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
