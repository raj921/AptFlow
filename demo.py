"""Offline walkthrough with isolated, automatically removed storage."""
import os
import tempfile
os.environ["MOCK_LLM"] = "1"
os.environ["BACKOFF_BASE"] = "0"
with tempfile.TemporaryDirectory() as directory:
    os.environ["DB_PATH"] = f"{directory}/demo.db"
    import engine
    engine.init()
    for message in ["Unit 4B sink leaking", "Unit 8 towels are dirty", "What is the wifi password?", "Gas smell in unit 5"]:
        engine.create_incident(message)
    for _ in range(10):
        if not engine.process_pending():
            break
    state = engine.snapshot()
    for incident in reversed(state["incidents"]):
        print(f"{incident['state']}: {incident['message']}")
        for event in reversed(state["events"]):
            if event["incident_id"] == incident["id"]:
                print(f"  {event['kind']}: {event['detail']}")
