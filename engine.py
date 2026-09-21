"""Ops engine: incident state machine driven by agents, with retries,
idempotent dispatch, and human escalation.

State flow:
  NEW -> TRIAGED -> DISPATCHED -> VERIFYING -> RESOLVED
                     |    ^           |
                     +----+  (bad evidence, re-dispatch, max 2)
                     +--> ESCALATED (uncertain triage / retries exhausted)
  ESCALATED --human--> RESOLVED | TRIAGED
"""
import json, os, random, sqlite3, threading, time, uuid

import agents

DB = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "ops.db"))
MAX_ATTEMPTS = 3
MAX_REDISPATCH = 2
BACKOFF_BASE = float(os.getenv("BACKOFF_BASE", "2"))  # evals set this to 0

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents(
  id TEXT PRIMARY KEY, state TEXT NOT NULL, category TEXT, severity TEXT,
  message TEXT NOT NULL, vendor_id TEXT, attempts INTEGER DEFAULT 0,
  next_run REAL DEFAULT 0, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id TEXT, kind TEXT, detail TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS vendors(
  id TEXT PRIMARY KEY, name TEXT, trade TEXT);
"""

_lock = threading.Lock()


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, conn() as c:
        c.executescript(SCHEMA)
        if not c.execute("SELECT 1 FROM vendors").fetchone():
            c.executemany("INSERT INTO vendors VALUES(?,?,?)", [
                ("v-rapid", "RapidRooter", "plumbing"),
                ("v-spark", "SparkClean", "cleaning"),
                ("v-hal", "HandyHal", "general"),
            ])


def log(c, incident_id, kind, detail):
    c.execute("INSERT INTO events(incident_id,kind,detail,ts) VALUES(?,?,?,?)",
              (incident_id, kind,
               detail if isinstance(detail, str) else json.dumps(detail), time.time()))


def create_incident(message: str, source: str = "guest") -> str:
    iid = uuid.uuid4().hex[:8]
    with _lock, conn() as c:
        c.execute("INSERT INTO incidents VALUES(?,?,?,?,?,?,0,0,?,?)",
                  (iid, "NEW", None, None, message, None, time.time(), time.time()))
        log(c, iid, "created", {"source": source, "message": message})
    return iid


class Retryable(Exception):
    """Step failed transiently; worker retries with backoff."""



def _send_job(incident: dict, vendor_id: str, idempotency_key: str) -> dict:
    """Simulated vendor webhook. Idempotency key means a retry after a timeout
    can't double-book the job — vendor side dedupes on this key."""
    # ponytail: random 30% reject to exercise the retry path in demos; real impl
    # is an HTTP POST with the same key in a header.
    if random.random() < 0.30:
        raise Retryable(f"vendor {vendor_id} rejected/timed out")
    return {"job_id": idempotency_key, "accepted_by": vendor_id}


def _collect_evidence(vendor_id: str) -> str:
    # ponytail: simulated vendor completion; real impl = webhook callback.
    return random.choice([
        "Replaced the P-trap, ran water for 10 min, tested dry. Photo attached.",
        "done",
        "Deep clean finished, linens replaced, checklist signed. Photo attached.",
        "looked at it",
    ])


def _escalate(c, inc, reason):
    c.execute("UPDATE incidents SET state='ESCALATED', updated=? WHERE id=?",
              (time.time(), inc["id"]))
    log(c, inc["id"], "escalated", reason)


def step(inc) -> None:
    """Run one state transition. Raises Retryable for transient failures."""
    with _lock, conn() as c:
        inc = dict(inc)
        iid = inc["id"]

        if inc["state"] == "NEW":
            d = agents.triage(inc["message"])
            log(c, iid, "triage", d)
            if d["confidence"] < 0.6:
                _escalate(c, inc, {"reason": "triage_uncertain", **d})
                return
            c.execute("UPDATE incidents SET state='TRIAGED',category=?,severity=?,updated=? WHERE id=?",
                      (d["category"], d["severity"], time.time(), iid))

        elif inc["state"] == "TRIAGED":
            vendors = [dict(r) for r in c.execute("SELECT * FROM vendors")]
            d = agents.dispatch(inc, vendors)
            log(c, iid, "dispatch_decision", d)
            attempts = inc["attempts"] + 1
            # Idempotency key: same incident + attempt => same key, safe to retry.
            key = f"{iid}:dispatch:{attempts}"
            job = _send_job(inc, d["vendor_id"], key)
            log(c, iid, "dispatched", job)
            c.execute("UPDATE incidents SET state='DISPATCHED',vendor_id=?,attempts=?,updated=? WHERE id=?",
                      (d["vendor_id"], attempts, time.time(), iid))

        elif inc["state"] == "DISPATCHED":
            evidence = _collect_evidence(inc["vendor_id"])
            log(c, iid, "evidence", evidence)
            d = agents.verify(inc, evidence)
            log(c, iid, "verify", d)
            if d["complete"] and d["confidence"] >= 0.6:
                reply = agents.guest_reply(inc, "resolved")
                log(c, iid, "guest_reply", reply)
                c.execute("UPDATE incidents SET state='RESOLVED',updated=? WHERE id=?",
                          (time.time(), iid))
            elif inc["attempts"] < MAX_REDISPATCH:
                log(c, iid, "redispatch", {"why": d["reasoning"]})
                c.execute("UPDATE incidents SET state='TRIAGED',updated=? WHERE id=?",
                          (time.time(), iid))
            else:
                _escalate(c, inc, {"reason": "verify_failed", **d})


def process_pending() -> int:
    """One worker sweep. Returns how many incidents were acted on."""
    now = time.time()
    with _lock, conn() as c:
        rows = c.execute(
            "SELECT * FROM incidents WHERE state IN ('NEW','TRIAGED','DISPATCHED') AND next_run<=?",
            (now,)).fetchall()
    acted = 0
    for row in rows:
        try:
            step(row)
            acted += 1
        except Retryable as e:
            with _lock, conn() as c:
                attempts = row["attempts"] + 1
                if attempts >= MAX_ATTEMPTS:
                    log(c, row["id"], "retry_exhausted", str(e))
                    _escalate(c, dict(row), {"reason": "retries_exhausted", "error": str(e)})
                else:
                    backoff = min(30, BACKOFF_BASE ** attempts) + random.random() * BACKOFF_BASE
                    log(c, row["id"], "retry", {"error": str(e), "attempt": attempts,
                                                 "backoff_s": round(backoff, 2)})
                    c.execute("UPDATE incidents SET attempts=?,next_run=?,updated=? WHERE id=?",
                              (attempts, now + backoff, time.time(), row["id"]))
        except Exception as e:  # agent/infra blew up — don't kill the worker
            with _lock, conn() as c:
                _escalate(c, dict(row), {"reason": "step_error", "error": str(e)})
    return acted


def human_action(incident_id: str, action: str, note: str = "") -> bool:
    """Human-in-the-loop: approve-close an escalation, or send it back out."""
    with _lock, conn() as c:
        row = c.execute("SELECT * FROM incidents WHERE id=? AND state='ESCALATED'",
                        (incident_id,)).fetchone()
        if not row:
            return False
        if action == "close":
            new = "RESOLVED"
        elif action == "redispatch":
            new = "TRIAGED"
        else:
            return False
        c.execute("UPDATE incidents SET state=?,attempts=0,next_run=0,updated=? WHERE id=?",
                  (new, time.time(), incident_id))
        log(c, incident_id, f"human_{action}", note)
    return True


def worker(stop: threading.Event, interval: float = 0.5):
    # ponytail: single worker thread — fine at apartment-portfolio volume.
    # Upgrade path: SQS/Postgres SKIP LOCKED when one worker isn't enough.
    init()
    while not stop.is_set():
        process_pending()
        stop.wait(interval)


def snapshot() -> dict:
    with _lock, conn() as c:
        return {
            "incidents": [dict(r) for r in c.execute(
                "SELECT * FROM incidents ORDER BY created DESC LIMIT 50")],
            "events": [dict(r) for r in c.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT 100")],
            "vendors": [dict(r) for r in c.execute("SELECT * FROM vendors")],
        }

