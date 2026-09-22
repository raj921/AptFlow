"""Offline fault-injection checks. Fixtures are synthetic; no provider calls or real vendors."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

os.environ["MOCK_LLM"] = "1"
os.environ["LANGSMITH_TRACING"] = "false"
import agents
import engine
from fastapi.testclient import TestClient
from main import app


def incident(iid):
    with engine.conn() as c:
        return dict(c.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone())


def drive(iid):
    for _ in range(20):
        engine.step({"id": iid})
        if incident(iid)["state"] in ("RESOLVED", "ESCALATED"):
            return incident(iid)["state"]
    raise AssertionError("Workflow did not reach a terminal state")


class ReliabilityChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = patch.multiple(engine, DB=f"{self.directory.name}/ops.db", BACKOFF_BASE=0)
        self.settings.start()
        engine.init()

    def tearDown(self):
        self.settings.stop()
        self.directory.cleanup()

    def test_ordinary_request_resolves_with_matching_evidence(self):
        iid = engine.create_incident("Unit 4B sink leaking")
        self.assertEqual(drive(iid), "RESOLVED")
        self.assertEqual(incident(iid)["vendor_id"], "v-rapid")

    def test_emergency_stops_before_vendor_dispatch(self):
        iid = engine.create_incident("Unit 4B: gas smell and smoke")
        self.assertEqual(drive(iid), "ESCALATED")
        self.assertEqual(incident(iid)["severity"], "critical")
        with engine.conn() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)
        with self.assertRaises(ValueError):
            engine.human_action(iid, "redispatch", "Retry")

    def test_uncertain_request_keeps_category_and_review_reason(self):
        iid = engine.create_incident("quick question about the place")
        self.assertEqual(drive(iid), "ESCALATED")
        self.assertEqual(incident(iid)["category"], "guest_support")
        self.assertTrue(incident(iid)["review_reason"])
        self.assertTrue(engine.human_action(iid, "close", "Answered checkout question"))
        self.assertFalse(engine.human_action(iid, "close", "Duplicate action"))

    def test_dispatch_uses_trade_not_category_alone(self):
        iid = engine.create_incident("AC broken in unit 7A")
        drive(iid)
        self.assertEqual(incident(iid)["vendor_id"], "v-hal")

    def test_guest_support_is_not_assigned_to_a_contractor(self):
        iid = engine.create_incident("What is the wifi password?")
        self.assertEqual(drive(iid), "ESCALATED")
        self.assertIsNone(incident(iid)["vendor_id"])

    def test_accepted_then_timeout_keeps_one_booking_key(self):
        iid = engine.create_incident("Sink leaking")
        send, keys = engine._send_job, []
        def ambiguous(inc, vendor, key):
            keys.append(key)
            result = send(inc, vendor, key)
            if len(keys) == 1:
                raise engine.Retryable("Response lost after acceptance")
            return result
        with patch.object(engine, "_send_job", ambiguous):
            self.assertEqual(drive(iid), "RESOLVED")
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1])
        with engine.conn() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)

    def test_permanent_transient_failure_has_exact_budget(self):
        iid = engine.create_incident("Sink leaking")
        with patch.object(engine, "_send_job", side_effect=engine.Retryable("timeout")) as send:
            self.assertEqual(drive(iid), "ESCALATED")
            self.assertEqual(send.call_count, 3)
        self.assertEqual(incident(iid)["retry_count"], 3)

    def test_backoff_is_persisted_and_not_immediately_retried(self):
        iid = engine.create_incident("Sink leaking")
        engine.step({"id": iid})
        with patch.object(engine, "BACKOFF_BASE", 2), patch.object(engine, "_send_job", side_effect=engine.Retryable()):
            engine.step({"id": iid})
        row = incident(iid)
        self.assertGreater(row["next_run"], time.time())
        self.assertFalse(engine.step({"id": iid}))

    def test_unrelated_evidence_never_resolves(self):
        iid = engine.create_incident("AC broken")
        with patch.object(engine, "_collect_evidence", return_value="Replaced the P-trap. Photo attached. Tested dry."):
            self.assertEqual(drive(iid), "ESCALATED")
        self.assertEqual(incident(iid)["attempts"], 2)

    def test_invalid_model_boolean_or_confidence_is_rejected(self):
        for decision in [dict(complete="false", confidence=.9, reasoning="wrong type"),
                         dict(complete=True, confidence=float("nan"), reasoning="NaN")]:
            iid = engine.create_incident("Sink leaking")
            with patch.object(agents, "verify", return_value=decision):
                self.assertEqual(drive(iid), "ESCALATED")

    def test_invented_vendor_is_blocked(self):
        iid = engine.create_incident("AC broken")
        with patch.object(agents, "dispatch", return_value=dict(vendor_id="made-up", confidence=1.0, reasoning="injection")):
            self.assertEqual(drive(iid), "ESCALATED")
        with engine.conn() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)

    def test_duplicate_sweeps_claim_once(self):
        iid = engine.create_incident("AC broken")
        entered, release = threading.Event(), threading.Event()
        original = agents.triage
        def slow(message):
            entered.set(); release.wait(3)
            return original(message)
        with patch.object(agents, "triage", slow), ThreadPoolExecutor(2) as pool:
            first = pool.submit(engine.step, {"id": iid})
            self.assertTrue(entered.wait(2))
            self.assertFalse(engine.step({"id": iid}))
            # Reads and writes remain possible while a model call is in flight.
            self.assertEqual(len(engine.snapshot()["incidents"]), 1)
            engine.create_incident("Other request")
            release.set()
            self.assertTrue(first.result())

    def test_stale_worker_cannot_commit(self):
        iid = engine.create_incident("AC broken")
        original = agents.triage
        def reclaim(message):
            with engine.conn() as c:
                c.execute("UPDATE incidents SET lease_token='new-owner' WHERE id=?", (iid,))
            return original(message)
        with patch.object(agents, "triage", reclaim):
            self.assertFalse(engine.step({"id": iid}))
        self.assertEqual(incident(iid)["state"], "NEW")

    def test_crash_after_booking_recovers_without_duplicate(self):
        iid = engine.create_incident("Sink leaking")
        engine.step({"id": iid})
        code = "import engine,os; inc=engine._claim(os.environ['TEST_IID']); engine._send_job(inc,'v-rapid',inc['id']+':dispatch:1'); os._exit(9)"
        env = {**os.environ, "DB_PATH": engine.DB, "TEST_IID": iid}
        result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True)
        self.assertEqual(result.returncode, 9)
        with engine.conn() as c:
            c.execute("UPDATE incidents SET lease_until=0 WHERE id=?", (iid,))
        self.assertEqual(drive(iid), "RESOLVED")
        with engine.conn() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)

    def test_ingress_idempotency_and_conflict(self):
        with ThreadPoolExecutor(8) as pool:
            ids = list(pool.map(lambda _: engine.create_incident("Sink leaking", key="same-event"), range(20)))
        self.assertEqual(len(set(ids)), 1)
        with self.assertRaises(ValueError):
            engine.create_incident("Other issue", key="same-event")

    def test_api_permissions_validation_conflicts_and_size(self):
        with patch.dict(os.environ, {"OPERATOR_PASSWORD": "test-only-password-long", "APP_ENV": "production"}):
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/state").status_code, 401)
                client.auth = ("operator", "test-only-password-long")
                self.assertEqual(client.get("/api/state").status_code, 200)
                for message in (" ", "a"*4001):
                    self.assertEqual(client.post("/api/events", json={"message": message}).status_code, 422)
                self.assertEqual(client.post("/api/events", content=b"x"*20000).status_code, 413)
                self.assertEqual(client.post("/api/events", json={"message": "x"}, headers={"Origin": "https://untrusted.example"}).status_code, 403)
                self.assertEqual(client.post("/api/incidents/missing/human", json={"action":"close","note":"Reviewed"}).status_code, 409)
                self.assertEqual(client.post("/api/incidents/missing/human", json={"action":"erase","note":"Reviewed"}).status_code, 422)
                self.assertEqual(client.post("/api/events", json={"message":"AC broken"}).status_code, 201)

    def test_langchain_json_adapter_and_provider_error_classification(self):
        import httpx2
        from langchain_openai import ChatOpenAI
        calls = []
        status = [200]
        def handler(request):
            calls.append(request)
            if status[0] != 200:
                return httpx2.Response(status[0], headers={"Retry-After":"12"}, json={"error":{"message":"fixture"}})
            decision = dict(category="maintenance", severity="high", confidence=.9, reasoning="Sink leaking")
            return httpx2.Response(200, json={"id":"fixture", "model":"fixture", "choices":[
                {"index":0, "finish_reason":"stop", "message":{"role":"assistant", "content":json.dumps(decision)}}],
                "usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}})
        with httpx2.Client(transport=httpx2.MockTransport(handler)) as client:
            agents.model.cache_clear()
            self.addCleanup(agents.model.cache_clear)
            with patch.object(agents, "MOCK", False), patch.object(agents, "MODEL", "deepseek-flash"), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fixture-only"}), patch.object(agents, "ChatOpenAI", side_effect=lambda **kwargs: ChatOpenAI(**kwargs, http_client=client)):
                self.assertEqual(agents.triage("Sink leaking")["category"], "maintenance")
                for code, error in [(429, agents.Retryable), (503, agents.Retryable), (401, RuntimeError)]:
                    status[0] = code
                    with self.assertRaises(error) as caught:
                        agents.triage("Sink leaking")
                    if code in (429, 503):
                        self.assertEqual(caught.exception.retry_after, 12)
        self.assertEqual(len(calls), 4, "SDK must not multiply queue retries")
        for request in calls:
            self.assertEqual(str(request.url), "https://api.deepseek.com/chat/completions")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "deepseek-flash")
            self.assertEqual(body["thinking"], {"type": "disabled"})
            self.assertEqual(body["response_format"], {"type": "json_object"})

    def test_low_confidence_dispatch_cannot_book(self):
        iid = engine.create_incident("AC broken")
        with patch.object(agents, "dispatch", return_value=dict(vendor_id="v-hal",confidence=.3,reasoning="Uncertain")):
            self.assertEqual(drive(iid), "ESCALATED")
        with engine.conn() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)

    def test_human_redispatch_uses_new_logical_booking(self):
        iid = engine.create_incident("Sink leaking")
        with patch.object(engine, "_collect_evidence", return_value="done"):
            self.assertEqual(drive(iid), "ESCALATED")
        self.assertTrue(engine.human_action(iid, "redispatch", "Operator confirmed access and repair scope"))
        self.assertEqual(drive(iid), "RESOLVED")
        with engine.conn() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0], 3)

    def test_load_100_requests_reaches_terminal_state(self):
        started = time.perf_counter()
        with ThreadPoolExecutor(8) as pool:
            ids = list(pool.map(lambda n: engine.create_incident(f"Unit {n} sink leaking", key=f"load-{n}"), range(100)))
        while any(incident(i)["state"] not in ("RESOLVED", "ESCALATED") for i in ids):
            engine.process_pending()
            self.assertLess(time.perf_counter()-started, 20)
        self.assertEqual(engine.snapshot()["counts"]["RESOLVED"], 100)
        with engine.conn() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0], 100)
        print(f"\n100 synthetic requests resolved in {time.perf_counter()-started:.2f}s; 4 worker slots; local SQLite")


if __name__ == "__main__":
    unittest.main(verbosity=2)
