"""End-to-end demo: seeds realistic events, runs the worker, prints the
transcript of every agent decision. Run: python3 demo.py
"""
import os, random, tempfile, threading, time

os.environ.setdefault("MOCK_LLM", "1")  # unset + REQUESTY_API_KEY for real LLM
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")
os.environ["BACKOFF_BASE"] = "0.3"

import engine

random.seed(3)

EVENTS = [
    "Water is pouring out from under the bathroom sink, please hurry",
    "We checked in and the towels are dirty, also trash was left",
    "What's the wifi password?",
    "hey so uh quick question about the place",
    "The AC is broken and there's a baby in the unit",
]

stop = threading.Event()
t = threading.Thread(target=engine.worker, args=(stop,), kwargs={"interval": 0.1}, daemon=True)
t.start()

for e in EVENTS:
    engine.create_incident(e)
time.sleep(4)
stop.set()

snap = engine.snapshot()
for inc in reversed(snap["incidents"]):
    print(f"\n=== #{inc['id']} [{inc['state']}] {inc['message'][:60]}")
    for ev in reversed([e for e in snap["events"] if e["incident_id"] == inc["id"]]):
        print(f"  {ev['kind']:>18} | {ev['detail'][:100]}")
