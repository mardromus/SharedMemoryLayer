import clsx from "clsx";
import { AlertTriangle, CheckCircle2, Info, Loader2, X, XCircle } from "lucide-react";
import { useEffect, useId, useRef, type ButtonHTMLAttributes, type KeyboardEvent, type ReactNode } from "react";
import { humanize } from "@/utils/format";

export function Button({
  variant = "primary",
  size = "md",
  loading,
  icon,
  children,
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger" | "success";
  size?: "sm" | "md";
  loading?: boolean;
  icon?: ReactNode;
}) {
  return (
    <button className={clsx("btn", `btn-${variant}`, size === "sm" && "btn-sm", className)} disabled={loading || rest.disabled} {...rest}>
      {loading ? <Loader2 className="spin" size={16} /> : icon}
      {children}
    </button>
  );
}

export function Card({ title, actions, children, className, subtitle }: { title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={clsx("card", className)}>
      {(title || actions) && (
        <header className="card-header">
          <div>
            {title && <h3 className="card-title">{title}</h3>}
            {subtitle && <p className="card-subtitle">{subtitle}</p>}
          </div>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      <div className="card-body">{children}</div>
    </section>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        {description && <p className="muted">{description}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  );
}

const TONES: Record<string, string> = {
  // statuses
  success: "green", allowed: "green", active: "green", verified: "green", up: "green", resolved: "green", ALLOWED: "green",
  denied: "red", failure: "red", rejected: "red", down: "red", DENIED: "red", deleted: "red", potential_conflict: "amber",
  suspended: "amber", archived: "slate", unverified: "slate", open: "amber", dismissed: "slate", none: "slate",
  // visibility
  private: "red", team: "blue", selected_teams: "violet", selected_agents: "violet", organization: "green",
  // sensitivity
  public: "green", internal: "slate", confidential: "amber", restricted: "red",
  // memory types
  knowledge: "blue", decision: "violet", learning: "teal", experience: "teal", context: "slate", task_result: "blue",
  incident: "red", documentation: "slate", solution: "green",
  // roles
  ADMIN: "red", TEAM_MANAGER: "violet", AGENT: "blue", READ_ONLY: "slate",
};

export function Badge({ value, tone, children, title }: { value?: string; tone?: string; children?: ReactNode; title?: string }) {
  const t = tone ?? (value ? TONES[value] : undefined) ?? "slate";
  return (
    <span className={clsx("badge", `badge-${t}`)} title={title}>
      {children ?? (value ? humanize(value) : "")}
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="spinner-row" role="status" aria-live="polite">
      <Loader2 className="spin" size={18} aria-hidden="true" /> {label ?? "Loading…"}
    </div>
  );
}

export function Empty({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="empty">
      {icon}
      <strong>{title}</strong>
      {children && <p className="muted">{children}</p>}
    </div>
  );
}

export function Alert({ tone = "info", title, children, onClose }: { tone?: "info" | "success" | "warning" | "danger"; title?: ReactNode; children?: ReactNode; onClose?: () => void }) {
  const Icon = { info: Info, success: CheckCircle2, warning: AlertTriangle, danger: XCircle }[tone];
  return (
    <div className={clsx("alert", `alert-${tone}`)} role={tone === "danger" ? "alert" : "status"}>
      <Icon size={18} className="alert-icon" />
      <div className="alert-content">
        {title && <strong>{title}</strong>}
        {children && <div>{children}</div>}
      </div>
      {onClose && (
        <button className="icon-btn" onClick={onClose} aria-label="Dismiss">
          <X size={16} />
        </button>
      )}
    </div>
  );
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Modal({ title, open, onClose, children, footer, wide }: { title: string; open: boolean; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    // Move focus into the dialog (first form control, else the dialog itself)
    const first = ref.current?.querySelector<HTMLElement>(".modal-body " + FOCUSABLE.split(", ").join(", .modal-body ")) ?? ref.current;
    first?.focus();
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key !== "Tab" || !ref.current) return;
      const nodes = [...ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((n) => n.offsetParent !== null);
      if (!nodes.length) return;
      const [firstNode, lastNode] = [nodes[0], nodes[nodes.length - 1]];
      if (e.shiftKey && document.activeElement === firstNode) { e.preventDefault(); lastNode.focus(); }
      else if (!e.shiftKey && document.activeElement === lastNode) { e.preventDefault(); firstNode.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div ref={ref} tabIndex={-1} className={clsx("modal", wide && "modal-wide")} role="dialog" aria-modal="true" aria-labelledby={titleId} onMouseDown={(e) => e.stopPropagation()}>
        <header className="modal-header">
          <h2 id={titleId}>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-footer">{footer}</footer>}
      </div>
    </div>
  );
}

export function Field({ label, hint, children, error }: { label: string; hint?: ReactNode; children: ReactNode; error?: string }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && !error && <span className="field-hint">{hint}</span>}
      {error && <span className="field-error">{error}</span>}
    </label>
  );
}

export function Meter({ value, label }: { value: number; label?: string }) {
  const pctValue = Math.max(0, Math.min(1, value)) * 100;
  const tone = value >= 0.75 ? "green" : value >= 0.5 ? "blue" : value >= 0.3 ? "amber" : "red";
  return (
    <div className="meter" role="meter" aria-valuemin={0} aria-valuemax={1} aria-valuenow={Number(value.toFixed(2))} aria-label={label ?? "score"} title={label ? `${label}: ${value.toFixed(2)}` : value.toFixed(2)}>
      <div className={clsx("meter-fill", `meter-${tone}`)} style={{ width: `${pctValue}%` }} />
    </div>
  );
}

export function Pagination({ page, pageSize, total, onChange }: { page: number; pageSize: number; total: number; onChange: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="pagination">
      <span className="muted">
        {total === 0 ? "No results" : `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} of ${total}`}
      </span>
      <div className="row gap-sm">
        <Button variant="secondary" size="sm" disabled={page <= 1} onClick={() => onChange(page - 1)}>
          Previous
        </Button>
        <Button variant="secondary" size="sm" disabled={page >= pages} onClick={() => onChange(page + 1)}>
          Next
        </Button>
      </div>
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange, label }: { tabs: { id: T; label: ReactNode }[]; value: T; onChange: (id: T) => void; label?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  // WAI-ARIA tabs pattern: arrow keys / Home / End move between tabs (roving tabindex)
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = tabs.findIndex((t) => t.id === value);
    const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    const target = tabs[(next + tabs.length) % tabs.length];
    onChange(target.id);
    requestAnimationFrame(() => ref.current?.querySelector<HTMLButtonElement>(`[data-tab="${target.id}"]`)?.focus());
  };
  return (
    <div className="tabs" role="tablist" aria-label={label} ref={ref} onKeyDown={onKeyDown}>
      {tabs.map((t) => (
        <button
          key={t.id}
          data-tab={t.id}
          role="tab"
          type="button"
          aria-selected={value === t.id}
          tabIndex={value === t.id ? 0 : -1}
          className={clsx("tab", value === t.id && "tab-active")}
          onClick={() => onChange(t.id)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function KeyValue({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([k, v], i) => (
        <div key={i} className="kv-row">
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}
