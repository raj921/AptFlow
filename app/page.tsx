"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  Inbox,
  Users,
  Plus,
  ArrowRight,
  Search,
  X,
  RefreshCw,
  Check,
  AlertTriangle,
  ShieldCheck,
  Wrench,
  Sparkles,
  ChevronDown,
  type LucideIcon,
} from "lucide-react";

type Incident = {
  id: string;
  state: string;
  category: string | null;
  severity: string | null;
  message: string;
  vendor_id: string | null;
  attempts: number;
  retry_count: number;
  next_run: number;
  updated: number;
  review_reason: string | null;
};
type EventRow = {
  id: number;
  incident_id: string;
  kind: string;
  detail: string;
  ts: number;
};
type Snapshot = {
  incidents: Incident[];
  events: EventRow[];
  vendors: { id: string; name: string; trade: string }[];
  counts: Record<string, number>;
  checks: number;
  system: {
    mode: string;
    vendor_mode: string;
    model: string | null;
    concurrency: number;
    worker_online: boolean;
    request_driven?: boolean;
  };
};
const tabs = [
  ["all", "All work"],
  ["action", "Needs review"],
  ["active", "In progress"],
  ["resolved", "Resolved"],
];
const icons: Record<string, LucideIcon> = {
  inbox: Inbox,
  users: Users,
  plus: Plus,
  arrow: ArrowRight,
  search: Search,
  close: X,
  refresh: RefreshCw,
  check: Check,
  alert: AlertTriangle,
  shield: ShieldCheck,
  wrench: Wrench,
  spark: Sparkles,
  chevron: ChevronDown,
};
function Icon({ name, size = 16 }: { name: string; size?: number }) {
  const Glyph = icons[name] ?? Inbox;
  return <Glyph size={size} strokeWidth={1.7} aria-hidden="true" />;
}
function label(value: string | null) {
  return (value ?? "Pending triage").replaceAll("_", " ");
}
function tone(state: string) {
  return state === "ESCALATED"
    ? "danger"
    : state === "RESOLVED"
      ? "success"
      : state === "DISPATCHED"
        ? "info"
        : "neutral";
}
function timeAgo(timestamp: number) {
  const minutes = Math.max(0, Math.floor((Date.now() / 1000 - timestamp) / 60));
  return minutes < 1
    ? "just now"
    : minutes < 60
      ? `${minutes}m ago`
      : `${Math.floor(minutes / 60)}h ago`;
}
function NumberValue({ value }: { value: number }) {
  return (
    <span className="t-digit-group is-animating" key={value}>
      {String(value)
        .split("")
        .map((n, i) => (
          <span className="t-digit" key={i}>
            {n}
          </span>
        ))}
    </span>
  );
}
function Chip({ state }: { state: string }) {
  return (
    <span className={`state-chip ${tone(state)}`}>
      <span />
      {state === "ESCALATED" ? "Needs review" : label(state)}
    </span>
  );
}
function detailText(event: EventRow) {
  try {
    const value = JSON.parse(event.detail);
    return (
      value.reasoning ??
      value.note ??
      value.message ??
      (event.kind === "retry"
        ? `Retry ${value.attempt} scheduled in ${value.delay_s}s`
        : JSON.stringify(value))
    );
  } catch {
    return event.detail;
  }
}
async function post(path: string, body: unknown, key?: string) {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(key ? { "Idempotency-Key": key } : {}),
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw new Error(
      typeof result.detail === "string"
        ? result.detail
        : "Request failed. Review your input and try again.",
    );
  }
  return response.json();
}

export default function OperationsPage() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<{
    incident: Incident;
    events: EventRow[];
  } | null>(null);
  const [tab, setTab] = useState("all");
  const [query, setQuery] = useState("");
  const [severity, setSeverity] = useState("all");
  const [message, setMessage] = useState("");
  const [note, setNote] = useState("");
  const [composeOpen, setComposeOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState("");
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const requestKey = useRef<string | null>(null);
  const inFlight = useRef(false);
  const returnFocus = useRef<HTMLElement | null>(null);
  const tabsRef = useRef<HTMLDivElement>(null);
  const pillRef = useRef<HTMLSpanElement>(null);

  async function refresh() {
    if (inFlight.current) return;
    inFlight.current = true;
    setRefreshing(true);
    try {
      const response = await fetch("/api/state", {
        cache: "no-store",
        signal: AbortSignal.timeout(8000),
      });
      if (!response.ok)
        throw new Error(
          response.status === 401
            ? "Sign-in required. Reload to sign in."
            : "Connection interrupted. Showing the last received data.",
        );
      setData(await response.json());
      setError("");
    } catch {
      setError(
        "Cannot reach the operations service. Showing the last received data; retry when it is available.",
      );
    } finally {
      inFlight.current = false;
      setRefreshing(false);
    }
  }
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 4000);
    return () => window.clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    const bar = tabsRef.current,
      pill = pillRef.current;
    if (!bar || !pill) return;
    const move = () => {
      const active = bar.querySelector<HTMLButtonElement>(
        '[aria-pressed="true"]',
      );
      if (active) {
        pill.style.transform = `translateX(${active.offsetLeft}px)`;
        pill.style.width = `${active.offsetWidth}px`;
      }
    };
    move();
    const observer = new ResizeObserver(move);
    observer.observe(bar);
    return () => observer.disconnect();
  }, [tab]);
  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    fetch(`/api/incidents/${selectedId}`, {
      signal: controller.signal,
      cache: "no-store",
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("Could not load this request");
        return response.json();
      })
      .then(setDetail)
      .catch((err) => {
        if (err.name !== "AbortError")
          setActionError(
            "Could not load full request history. Retrying with the next update.",
          );
      });
    return () => controller.abort();
  }, [selectedId, data]);
  const incidents = data?.incidents ?? [];
  const events = data?.events ?? [];
  const vendors = data?.vendors ?? [];
  const selected =
    incidents.find((i) => i.id === selectedId) ??
    (detail?.incident.id === selectedId ? detail.incident : null);
  const selectedEvents =
    detail?.incident.id === selectedId
      ? detail.events
      : events.filter((e) => e.incident_id === selectedId);
  const counts = data?.counts ?? {};
  const humanCount = counts.ESCALATED ?? 0;
  const openCount = Object.entries(counts).reduce(
    (n, [state, count]) => n + (state !== "RESOLVED" ? count : 0),
    0,
  );
  const filtered = useMemo(
    () =>
      incidents.filter(
        (i) =>
          (tab === "all" ||
            (tab === "action" && i.state === "ESCALATED") ||
            (tab === "resolved" && i.state === "RESOLVED") ||
            (tab === "active" &&
              ["NEW", "TRIAGED", "DISPATCHED"].includes(i.state))) &&
          (severity === "all" || i.severity === severity) &&
          `${i.id} ${i.message} ${i.category ?? ""}`
            .toLowerCase()
            .includes(query.toLowerCase()),
      ),
    [incidents, tab, query, severity],
  );
  const vendorName = (id: string | null) =>
    vendors.find((v) => v.id === id)?.name ?? "Unassigned";
  const online = Boolean(data?.system.worker_online && !error);
  async function createIncident(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !message.trim()) return;
    setBusy(true);
    setActionError("");
    requestKey.current ??= crypto.randomUUID();
    try {
      const result = await post(
        "/api/events",
        { message: message.trim(), source: "operator" },
        requestKey.current,
      );
      setMessage("");
      requestKey.current = null;
      setComposeOpen(false);
      setTab("all");
      setQuery("");
      setSeverity("all");
      setToast("Request received. Processing will appear in the queue.");
      await refresh();
      setSelectedId(result.incident_id);
      setNote("");
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "Could not submit. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function humanAction(action: "close" | "redispatch") {
    if (!selected || busy || !note.trim()) return;
    setBusy(true);
    setActionError("");
    try {
      await post(`/api/incidents/${selected.id}/human`, { action, note });
      await refresh();
      setSelectedId(null);
      setNote("");
      setToast(
        action === "close"
          ? "Review recorded. Request resolved."
          : "Review recorded. Dispatch will retry.",
      );
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "Review could not be saved.",
      );
      await refresh();
    } finally {
      setBusy(false);
    }
  }
  function openIncident(id: string) {
    returnFocus.current = document.activeElement as HTMLElement;
    setSelectedId(id);
    setNote("");
    setActionError("");
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <span />
            <span />
            <span />
          </div>
          <div>
            <strong>hyatus</strong>
            <small>operations</small>
          </div>
        </div>
        <div className="workspace-label">WORKSPACE</div>
        <nav className="nav" aria-label="Main navigation">
          <a href="#queue" className="nav-item active">
            <Icon name="inbox" />
            Requests <b>{openCount}</b>
          </a>
          <a href="#vendors" className="nav-item">
            <Icon name="users" />
            Vendors <b>{vendors.length}</b>
          </a>
          <a href="#trace" className="nav-item">
            <Icon name="shield" />
            Activity log
          </a>
        </nav>
        <div className="sidebar-rule" />
        <div className="workspace-label">SERVICE STATUS</div>
        <div className="system-card">
          <span className={online ? "pulse" : "offline-dot"} />
          <div>
            <strong>
              {online ? "Worker responding" : "Worker unavailable"}
            </strong>
            <small>
              {error
                ? "Connection interrupted"
                : data
                  ? data.system.request_driven
                    ? "Request-driven processing"
                    : `${data.system.concurrency} parallel slots`
                  : "Connecting…"}
            </small>
          </div>
        </div>
        <div className="sidebar-foot">
          <div className="profile">
            <span>OP</span>
            <div>
              <strong>Operator workspace</strong>
              <small>Single workspace pilot</small>
            </div>
          </div>
        </div>
      </aside>
      <section className="content">
        <header className="topbar">
          <div className="crumbs">
            <span>Apartments</span>
            <Icon name="chevron" />
            <strong>Requests</strong>
          </div>
          <div className="top-actions">
            <span className="live-label">
              {online ? "Connected" : "Offline"}
            </span>
            <button
              className="icon-button"
              aria-label="Refresh requests"
              disabled={refreshing}
              onClick={() => void refresh()}
            >
              <Icon name="refresh" />
            </button>
          </div>
        </header>
        <div className="mode-banner">
          {data?.system.mode === "live-model"
            ? `Live model: ${data.system.model}`
            : "Demo decisions"}{" "}
          · Simulated vendors and completion evidence · Guest updates are drafts
        </div>
        <div className="page-head">
          <div>
            <div className="eyebrow">
              <span className="eyebrow-line" />
              APARTMENT OPERATIONS
            </div>
            <h1>
              Every request.
              <br />
              <em>Accounted for.</em>
            </h1>
            <p>
              Review urgent issues, follow assigned work, and see what supports
              each resolution.
            </p>
          </div>
          <button
            className="primary-button"
            onClick={() => {
              returnFocus.current = document.activeElement as HTMLElement;
              setActionError("");
              setComposeOpen(true);
            }}
          >
            <Icon name="plus" />
            New request
          </button>
        </div>
        <div className="metric-grid" aria-label="Operations summary">
          {[
            ["Open requests", openCount, "Across all recorded requests"],
            ["Needs review", humanCount, "An operator decision is needed"],
            ["Resolved", counts.RESOLVED ?? 0, "Includes manual resolutions"],
            [
              "Evidence reviews",
              data?.checks ?? 0,
              "Recorded verification decisions",
            ],
          ].map(([title, value, description], i) => (
            <div className={`metric-card ${i === 1 ? "warm" : ""}`} key={title}>
              <span>{title}</span>
              <strong>
                {data ? <NumberValue value={Number(value)} /> : "—"}
              </strong>
              <small>{description}</small>
            </div>
          ))}
        </div>
        <div className="section-head" id="queue">
          <div>
            <div className="section-kicker">01 / REQUESTS</div>
            <h2>
              Work queue <span>{filtered.length}</span>
            </h2>
          </div>
          <div className="filter-tools">
            <label className="search">
              <Icon name="search" />
              <input
                aria-label="Search requests"
                placeholder="Search requests"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <select
              className="filter-button"
              aria-label="Filter by severity"
              value={severity}
              onChange={(e) => setSeverity(e.target.value)}
            >
              <option value="all">All severities</option>
              {["critical", "high", "medium", "low"].map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div
          className="tabs t-tabs"
          ref={tabsRef}
          role="group"
          aria-label="Filter by status"
        >
          <span className="t-tabs-pill" ref={pillRef} aria-hidden="true" />
          {tabs.map(([id, name]) => (
            <button
              key={id}
              className={`tab t-tab ${tab === id ? "active" : ""}`}
              aria-pressed={tab === id}
              onClick={() => setTab(id)}
            >
              {name}
            </button>
          ))}
        </div>
        {error && (
          <div className="error-banner" role="alert">
            <Icon name="alert" />
            <span>{error}</span>
            <button onClick={() => void refresh()}>Retry</button>
          </div>
        )}
        <div className="queue-list" aria-busy={!data && !error}>
          <div className="list-header">
            <span>Request</span>
            <span>Category</span>
            <span>Assigned vendor</span>
            <span>Status</span>
            <span>Updated</span>
            <span />
          </div>
          {!data && !error ? (
            <div
              className="queue-skeleton"
              role="status"
              aria-label="Loading requests"
            >
              {[1, 2, 3].map((n) => (
                <div className="skeleton-row" key={n}>
                  <span />
                  <span />
                  <span />
                  <span />
                </div>
              ))}
            </div>
          ) : !filtered.length ? (
            <div className="empty">
              <Icon name="inbox" size={24} />
              <strong>
                {error ? "Requests unavailable" : "No matching requests"}
              </strong>
              <p>Adjust the filters or add a request.</p>
            </div>
          ) : (
            filtered.map((i) => (
              <button
                className="incident-row"
                key={i.id}
                onClick={() => openIncident(i.id)}
              >
                <span className="incident-title">
                  <span className={`severity ${i.severity ?? "low"}`} />
                  <span>
                    <strong>{i.message}</strong>
                    <small>
                      #{i.id.slice(0, 8)} ·{" "}
                      {i.retry_count > 0 ? `retry ${i.retry_count}/3 · ` : ""}
                      {label(i.severity)}
                    </small>
                  </span>
                </span>
                <span className="route">
                  <Icon
                    name={i.category === "housekeeping" ? "spark" : "wrench"}
                  />
                  {label(i.category)}
                </span>
                <span className="owner">{vendorName(i.vendor_id)}</span>
                <Chip state={i.state} />
                <span className="updated">{timeAgo(i.updated)}</span>
                <span className="row-arrow">
                  <Icon name="arrow" />
                </span>
              </button>
            ))
          )}
        </div>
        <p className="muted">
          Showing up to 100 recent requests. Summary counts include all
          requests.
        </p>
        <div className="lower-grid">
          <section className="surface event-surface" id="trace">
            <div className="surface-head">
              <div>
                <div className="section-kicker">02 / ACTIVITY</div>
                <h2>Latest decisions</h2>
              </div>
              <span className="muted">Recorded by the service</span>
            </div>
            {events
              .filter((e) => e.kind !== "step_timing")
              .slice(0, 7)
              .map((e) => (
                <button
                  className="event-line trace-button"
                  key={e.id}
                  onClick={() => openIncident(e.incident_id)}
                >
                  <span className="event-icon">
                    <Icon name={e.kind === "escalated" ? "alert" : "check"} />
                  </span>
                  <span>
                    <strong>{label(e.kind)}</strong>
                    <small>{detailText(e)}</small>
                  </span>
                  <time>{timeAgo(e.ts)}</time>
                </button>
              ))}
            {!events.length && (
              <p className="surface-empty">No decisions recorded yet.</p>
            )}
          </section>
          <section className="surface vendor-surface" id="vendors">
            <div className="surface-head">
              <div>
                <div className="section-kicker">03 / VENDORS</div>
                <h2>Demo directory</h2>
              </div>
              <span className="muted">Simulated</span>
            </div>
            {vendors.map((v) => (
              <div className="vendor-line" key={v.id}>
                <span className="vendor-initial">{v.name[0]}</span>
                <span>
                  <strong>{v.name}</strong>
                  <small>{v.trade}</small>
                </span>
                <span className="muted">
                  {
                    incidents.filter(
                      (i) => i.vendor_id === v.id && i.state === "DISPATCHED",
                    ).length
                  }{" "}
                  active
                </span>
              </div>
            ))}
            <p className="surface-empty">
              Emergency and guest-support requests require operator handling. No
              real vendor is contacted.
            </p>
          </section>
        </div>
        <footer className="footer">
          <span>hyatus ops / single workspace pilot</span>
          <span>Classification → eligible vendor → evidence review</span>
        </footer>
      </section>

      <Dialog.Root
        open={composeOpen}
        onOpenChange={(open) => {
          if (!busy) setComposeOpen(open);
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="overlay" />
          <Dialog.Content
            className="compose-sheet modal-content"
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              returnFocus.current?.focus();
            }}
          >
            <form onSubmit={createIncident}>
              <div className="sheet-top">
                <Dialog.Title>New apartment request</Dialog.Title>
                <Dialog.Close
                  className="icon-button"
                  disabled={busy}
                  aria-label="Close new request"
                >
                  <Icon name="close" />
                </Dialog.Close>
              </div>
              <Dialog.Description className="sheet-copy">
                Include the apartment or unit and what happened. Emergency
                signals will stop automation for operator review.
              </Dialog.Description>
              <label htmlFor="message" className="field-label">
                Request details
              </label>
              <textarea
                id="message"
                required
                maxLength={4000}
                value={message}
                disabled={busy}
                onChange={(e) => {
                  setMessage(e.target.value);
                  requestKey.current = null;
                }}
                placeholder="Unit 4B: the kitchen sink is leaking…"
              />
              {actionError && (
                <p className="error-banner" role="alert">
                  {actionError}
                </p>
              )}
              <div className="sheet-foot">
                <span>{message.length}/4000</span>
                <button
                  className="primary-button"
                  disabled={busy || !message.trim()}
                  type="submit"
                >
                  {busy ? "Submitting…" : "Submit request"}
                  <Icon name="arrow" />
                </button>
              </div>
            </form>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
      <Dialog.Root
        open={Boolean(selected)}
        onOpenChange={(open) => {
          if (!open && !busy) setSelectedId(null);
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="overlay" />
          <Dialog.Content
            className="detail-sheet drawer-content"
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              returnFocus.current?.focus();
            }}
          >
            <div className="sheet-top">
              <span className="section-kicker">
                REQUEST / #{selected?.id.slice(0, 8)}
              </span>
              <Dialog.Close
                className="icon-button"
                aria-label="Close request details"
                disabled={busy}
              >
                <Icon name="close" />
              </Dialog.Close>
            </div>
            <Dialog.Title>{selected?.message}</Dialog.Title>
            <Dialog.Description className="sheet-copy">
              Follow this request and review the recorded evidence below.
            </Dialog.Description>
            {selected && (
              <>
                <Chip state={selected.state} />
                <div className="detail-meta">
                  <span>
                    <small>Category</small>
                    {label(selected.category)}
                  </span>
                  <span>
                    <small>Severity</small>
                    {label(selected.severity)}
                  </span>
                  <span>
                    <small>Bookings / retries</small>
                    {selected.attempts} / {selected.retry_count}
                  </span>
                </div>
                <div className="path">
                  {["Triage", "Dispatch", "Closeout"].map((step, i) => (
                    <div
                      className={`path-step ${(i === 0 && selected.category) || (i === 1 && selected.vendor_id) || (i === 2 && selected.state === "RESOLVED") ? "done" : ""}`}
                      key={step}
                    >
                      <span>0{i + 1}</span>
                      <div>
                        <strong>{step}</strong>
                        <small>
                          {i === 1
                            ? vendorName(selected.vendor_id)
                            : i === 2
                              ? "See evidence and review below"
                              : "Category and urgency"}
                        </small>
                      </div>
                    </div>
                  ))}
                </div>
                {selected.state === "ESCALATED" ? (
                  <form
                    className="human-actions"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void humanAction("close");
                    }}
                  >
                    <div>
                      <Icon name="alert" />
                      <strong>Operator review required</strong>
                      <small>
                        {selected.review_reason ??
                          "Review the request and recorded decisions."}
                      </small>
                    </div>
                    <label className="field-label" htmlFor="review-note">
                      What did you check or do?
                    </label>
                    <textarea
                      id="review-note"
                      required
                      value={note}
                      maxLength={4000}
                      disabled={busy}
                      onChange={(e) => setNote(e.target.value)}
                    />
                    {selected.category &&
                      selected.category !== "guest_support" &&
                      selected.severity !== "critical" && (
                        <button
                          type="button"
                          className="secondary-button"
                          disabled={busy || !note.trim()}
                          onClick={() => void humanAction("redispatch")}
                        >
                          Retry dispatch
                        </button>
                      )}
                    <button
                      className="primary-button"
                      type="submit"
                      disabled={busy || !note.trim()}
                    >
                      {busy ? "Saving…" : "Mark resolved"}
                    </button>
                  </form>
                ) : (
                  <div className="safe-note">
                    <Icon name="shield" />
                    <span>
                      {selected.state === "RESOLVED"
                        ? "Closed. Review evidence or the operator note below."
                        : selected.retry_count
                          ? "A transient failure is scheduled for retry."
                          : "Processing. Status updates automatically."}
                    </span>
                  </div>
                )}
                {actionError && (
                  <p className="error-banner" role="alert">
                    {actionError}
                  </p>
                )}
                <h3 className="detail-label">RECENT REQUEST HISTORY</h3>
                <div className="detail-history">
                  {selectedEvents
                    .filter((e) => e.kind !== "step_timing")
                    .map((e) => (
                      <div key={e.id}>
                        <strong>{label(e.kind)}</strong>
                        <p>{detailText(e)}</p>
                        <time>{timeAgo(e.ts)}</time>
                      </div>
                    ))}
                </div>
              </>
            )}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
      <div
        className={`toast ${toast ? "is-open" : ""}`}
        role="status"
        aria-live="polite"
      >
        {toast && (
          <>
            <Icon name="check" />
            {toast}
          </>
        )}
      </div>
    </main>
  );
}
