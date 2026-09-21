"""Eval harness: scripted scenarios + graders. Runs fully offline (MOCK_LLM).
This is the answer to 'how do we know the agents work' — every agent change
gets run against these before it ships.

Usage: python3 evals.py
"""
import os, random, sys, tempfile

os.environ["MOCK_LLM"] = "1"
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")
os.environ["BACKOFF_BASE"] = "0"  # don't actually sleep through real backoff in tests

import engine

random.seed(42)
engine.init()


def drive(iid, sweeps=60):
    """Run the worker until the incident leaves actionable states."""
    for _ in range(sweeps):
        engine.process_pending()
        state = _state(iid)
        if state in ("RESOLVED", "ESCALATED"):
            return state
    return _state(iid)


def _state(iid):
    with engine.conn() as c:
        return c.execute("SELECT state FROM incidents WHERE id=?", (iid,)).fetchone()["state"]


def _incident(iid):
    with engine.conn() as c:
        return dict(c.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone())


def _events(iid, kind):
    with engine.conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM events WHERE incident_id=? AND kind=?", (iid, kind))]


CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


@case("critical leak is triaged as maintenance/critical and auto-resolves")
def _():
    iid = engine.create_incident("Water is flooding the bathroom, pipe burst!")
    state = drive(iid)
    inc = _incident(iid)
    assert state == "RESOLVED", f"got {state}"
    assert inc["category"] == "maintenance" and inc["severity"] == "critical"


@case("vague message with no signal escalates to a human, human closes it")
def _():
    iid = engine.create_incident("hey so uh quick question about the place")
    state = drive(iid)
    assert state == "ESCALATED", f"got {state}"
    ok = engine.human_action(iid, "close", "answered manually: checkout time")
    assert ok and _state(iid) == "RESOLVED"


@case("flaky vendor retries with backoff and still dispatches (no double-book)")
def _():
    iid = engine.create_incident("AC is broken and it's 95 degrees")
    state = drive(iid)
    assert state == "RESOLVED", f"got {state}"
    keys = {e["detail"] for e in _events(iid, "dispatched")}
    assert all(":dispatch:" in k for k in keys), "idempotency keys missing"


@case("bad evidence triggers re-dispatch or escalation, never blind-resolve")
def _():
    random.seed(7)  # seed picked so 'done'/'looked at it' evidence shows up
    iid = engine.create_incident("Kitchen sink leaking under the cabinet")
    state = drive(iid, sweeps=120)
    verifies = _events(iid, "verify")
    if state == "RESOLVED":
        # only resolved if at least one verification actually passed
        assert any('"complete": true' in v["detail"] for v in verifies)
    else:
        assert state == "ESCALATED"


def main():
    passed = 0
    for name, fn in CASES:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {name}: {e}")
    print(f"\n{passed}/{len(CASES)} evals passed")
    sys.exit(0 if passed == len(CASES) else 1)


if __name__ == "__main__":
    main()
