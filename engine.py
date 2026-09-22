"""Durable single-host queue; LangGraph decisions; fenced commits; bounded workers."""
import json
import os
import random
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
import agents

DB = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "ops.db"))
MAX_ATTEMPTS = 3
MAX_REDISPATCH = 2
BACKOFF_BASE = float(os.getenv("BACKOFF_BASE", "2"))
CONCURRENCY = min(8, max(1, int(os.getenv("WORKER_CONCURRENCY", "4"))))
LEASE_SECONDS = 120
Retryable = agents.Retryable
SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents(
 id TEXT PRIMARY KEY, state TEXT NOT NULL, category TEXT, severity TEXT,
 message TEXT NOT NULL, vendor_id TEXT, attempts INTEGER DEFAULT 0,
 next_run REAL DEFAULT 0, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT, kind TEXT, detail TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS vendors(id TEXT PRIMARY KEY, name TEXT, trade TEXT);
CREATE TABLE IF NOT EXISTS submissions(key TEXT PRIMARY KEY, incident_id TEXT, message TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS jobs(key TEXT PRIMARY KEY, incident_id TEXT, vendor_id TEXT, created REAL);
CREATE TABLE IF NOT EXISTS health(id INTEGER PRIMARY KEY CHECK(id=1), heartbeat REAL, error TEXT);
CREATE INDEX IF NOT EXISTS pending ON incidents(state,next_run);
CREATE INDEX IF NOT EXISTS incident_trace ON events(incident_id,id);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB, timeout=5)
    c.row_factory = sqlite3.Row
    try:
        with c:
            yield c
    finally:
        c.close()


def init():
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        c.execute("BEGIN IMMEDIATE")
        columns = {r[1] for r in c.execute("PRAGMA table_info(incidents)")}
        for name, definition in {"retry_count": "INTEGER DEFAULT 0", "lease_until": "REAL DEFAULT 0",
                                 "lease_token": "TEXT", "review_reason": "TEXT"}.items():
            if name not in columns:
                c.execute(f"ALTER TABLE incidents ADD COLUMN {name} {definition}")
        c.executemany("INSERT OR IGNORE INTO vendors VALUES(?,?,?)", [
            ("v-rapid", "RapidRooter", "plumbing"), ("v-spark", "SparkClean", "cleaning"),
            ("v-hal", "HandyHal", "general")])


def log(c, iid, kind, detail):
    c.execute("INSERT INTO events(incident_id,kind,detail,ts) VALUES(?,?,?,?)",
              (iid, kind, detail if isinstance(detail, str) else json.dumps(detail), time.time()))


def create_incident(message, source="guest", key=None):
    message = message.strip()
    if not message or len(message) > 4000 or len(source) > 40:
        raise ValueError("A message of 1–4000 characters is required")
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        if key:
            previous = c.execute("SELECT * FROM submissions WHERE key=?", (key,)).fetchone()
            if previous:
                if (message, source) != (previous["message"], previous["source"]):
                    raise ValueError("Request key was already used for a different event")
                return previous["incident_id"]
        if c.execute("SELECT count(*) FROM incidents WHERE state IN ('NEW','TRIAGED','DISPATCHED')").fetchone()[0] >= 1000:
            raise OverflowError("Queue at capacity; try again later")
        iid = uuid.uuid4().hex
        c.execute("INSERT INTO incidents(id,state,message,created,updated) VALUES(?,?,?,?,?)",
                  (iid, "NEW", message, time.time(), time.time()))
        if key:
            c.execute("INSERT INTO submissions VALUES(?,?,?,?)", (key, iid, message, source))
        log(c, iid, "created", {"source": source, "message": message})
        return iid


def _send_job(incident, vendor_id, key):
    # ponytail: persistent simulated receiver. Real receivers must honor this same key.
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute("INSERT OR IGNORE INTO jobs VALUES(?,?,?,?)", (key, incident["id"], vendor_id, time.time()))
        job = c.execute("SELECT * FROM jobs WHERE key=?", (key,)).fetchone()
        if job["vendor_id"] != vendor_id:
            raise ValueError("Job key cannot change vendor")
        return dict(job_id=key, accepted_by=vendor_id, simulated=True)


def _collect_evidence(incident):
    return f"SIMULATED completion for {incident['id']}: {incident['message']}. Repair and matching checklist recorded."


class Flow(TypedDict):
    incident: dict
    outcome: dict


def _triage(state):
    inc = state["incident"]
    d = agents.Triage.model_validate(agents.triage(inc["message"])).model_dump()
    reason = "Emergency: operator must assess before dispatch" if d["severity"] == "critical" else (
        "More information is needed" if d["confidence"] < 0.6 else None)
    return {"outcome": dict(state="ESCALATED" if reason else "TRIAGED", category=d["category"],
                            severity=d["severity"], review_reason=reason, events=[("triage", d)])}


def _dispatch(state):
    inc = state["incident"]
    if inc["severity"] == "critical":
        return {"outcome": dict(state="ESCALATED", review_reason="Emergency requires operator handling", events=[])}
    with conn() as c:
        vendors = [dict(r) for r in c.execute("SELECT * FROM vendors")]
    d = agents.dispatch(inc, vendors)
    if d["confidence"] < 0.6 or d["vendor_id"] not in {v["id"] for v in vendors}:
        return {"outcome": dict(state="ESCALATED", review_reason=d["reasoning"], events=[("dispatch_decision", d)])}
    # attempts counts logical bookings, retry_count counts network retries.
    key = f"{inc['id']}:dispatch:{inc['attempts'] + 1}"
    job = _send_job(inc, d["vendor_id"], key)
    return {"outcome": dict(state="DISPATCHED", vendor_id=d["vendor_id"], attempts=inc["attempts"] + 1,
                            events=[("dispatch_decision", d), ("dispatched", job)])}


def _verify(state):
    inc = state["incident"]
    evidence = _collect_evidence(inc)
    d = agents.Verification.model_validate(agents.verify(inc, evidence)).model_dump()
    events = [("evidence", evidence), ("verify", d)]
    if d["complete"] and d["confidence"] >= 0.6:
        events.append(("guest_reply_draft", agents.guest_reply(inc, "resolved")))
        return {"outcome": dict(state="RESOLVED", events=events)}
    if inc["attempts"] < MAX_REDISPATCH:
        return {"outcome": dict(state="TRIAGED", events=events + [("redispatch", d["reasoning"])])}
    return {"outcome": dict(state="ESCALATED", review_reason="Evidence did not establish resolution", events=events)}


# Each invocation executes one decision; SQL persists the resulting transition and trace atomically.
# No second checkpoint store. Retry timing and human waits live in the durable queue.
_graph = StateGraph(Flow)
for name, node in [("triage", _triage), ("dispatch", _dispatch), ("verify", _verify)]:
    _graph.add_node(name, node)
    _graph.add_edge(name, END)
_graph.add_conditional_edges(START, lambda s: {"NEW": "triage", "TRIAGED": "dispatch", "DISPATCHED": "verify"}[s["incident"]["state"]])
workflow = _graph.compile()


def _claim(iid):
    now, token = time.time(), uuid.uuid4().hex
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        changed = c.execute("UPDATE incidents SET lease_until=?,lease_token=? WHERE id=? "
                            "AND state IN ('NEW','TRIAGED','DISPATCHED') AND next_run<=? AND lease_until<=?",
                            (now + LEASE_SECONDS, token, iid, now, now)).rowcount
        if not changed:
            return None
        return dict(c.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone())


def step(row):
    inc = _claim(row["id"])
    if not inc:
        return False
    started = time.perf_counter()
    try:
        outcome = workflow.invoke({"incident": inc})["outcome"]
        outcome.update(retry_count=0, next_run=0)
    except Retryable as error:
        count = inc["retry_count"] + 1
        if count >= MAX_ATTEMPTS:
            outcome = dict(state="ESCALATED", retry_count=count, review_reason="Transient failures exhausted the retry budget",
                           events=[("retry_exhausted", type(error).__name__)])
        else:
            delay = min(30, BACKOFF_BASE * 2 ** (count - 1)) * random.uniform(0.5, 1)
            delay = max(delay, error.retry_after)
            outcome = dict(retry_count=count, next_run=time.time() + delay,
                           events=[("retry", {"attempt": count, "delay_s": round(delay, 3)})])
    except Exception as error:
        outcome = dict(state="ESCALATED", review_reason=f"Decision failed validation or execution ({type(error).__name__})",
                       events=[("step_error", type(error).__name__)])
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        current = c.execute("SELECT lease_token FROM incidents WHERE id=?", (inc["id"],)).fetchone()
        if current["lease_token"] != inc["lease_token"]:
            return False  # Expired worker cannot overwrite a newer claim.
        events = outcome.pop("events")
        outcome.update(lease_until=0, lease_token=None, updated=time.time())
        fields = ",".join(f"{field}=?" for field in outcome)
        c.execute(f"UPDATE incidents SET {fields} WHERE id=?", (*outcome.values(), inc["id"]))
        for kind, detail in events:
            log(c, inc["id"], kind, detail)
        if outcome.get("state") == "ESCALATED":
            log(c, inc["id"], "escalated", outcome["review_reason"])
        log(c, inc["id"], "step_timing", {"from": inc["state"], "ms": round((time.perf_counter()-started)*1000, 1)})
    return True


def process_pending():
    with conn() as c:
        rows = c.execute("SELECT id FROM incidents WHERE state IN ('NEW','TRIAGED','DISPATCHED') "
                         "AND next_run<=? AND lease_until<=? ORDER BY created LIMIT ?",
                         (time.time(), time.time(), CONCURRENCY)).fetchall()
    # ponytail: independent incidents run concurrently on one host; no network calls inside DB transactions.
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        return sum(pool.map(step, rows))


def human_action(iid, action, note="", actor="local-operator"):
    if action not in ("close", "redispatch") or not note.strip():
        raise ValueError("Choose a valid action and give a review note")
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM incidents WHERE id=? AND state='ESCALATED'", (iid,)).fetchone()
        if not row:
            return False
        if action == "redispatch" and (row["severity"] == "critical" or row["category"] == "guest_support" or not row["category"]):
            raise ValueError("This request requires operator handling; automatic dispatch is unavailable")
        new = "RESOLVED" if action == "close" else "TRIAGED"
        # Preserve booking generation so a human-authorized new dispatch receives a new key.
        c.execute("UPDATE incidents SET state=?,retry_count=0,next_run=0,review_reason=NULL,updated=? WHERE id=?", (new, time.time(), iid))
        log(c, iid, f"human_{action}", {"note": note.strip(), "actor": actor})
        return True


def worker(stop: threading.Event, interval=0.5):
    while not stop.is_set():
        error = None
        try:
            process_pending()
        except Exception as exc:
            error = type(exc).__name__
        try:
            with conn() as c:
                c.execute("INSERT OR REPLACE INTO health VALUES(1,?,?)", (time.time(), error))
        except sqlite3.Error:
            pass  # Next successful sweep restores health; stale heartbeat reports failure.
        stop.wait(interval)


def snapshot():
    with conn() as c:
        health = c.execute("SELECT * FROM health WHERE id=1").fetchone()
        counts = {r[0]: r[1] for r in c.execute("SELECT state,count(*) FROM incidents GROUP BY state")}
        return dict(
            incidents=[dict(r) for r in c.execute("SELECT * FROM incidents ORDER BY created DESC LIMIT 100")],
            events=[dict(r) for r in c.execute("SELECT * FROM events ORDER BY id DESC LIMIT 100")],
            vendors=[dict(r) for r in c.execute("SELECT * FROM vendors")],
            counts=counts,
            checks=c.execute("SELECT count(*) FROM events WHERE kind='verify'").fetchone()[0],
            system=dict(mode="demo" if agents.MOCK else "live-model", vendor_mode="simulated",
                        model=None if agents.MOCK else agents.MODEL, concurrency=CONCURRENCY,
                        worker_online=bool(health and not health["error"] and time.time()-health["heartbeat"] < 45)))
