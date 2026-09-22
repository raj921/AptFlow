"""Credential-safe catalog lookup, then opt-in live evaluation on synthetic requests."""
import argparse
import json
import os
import sys
import time
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
parser = argparse.ArgumentParser()
parser.add_argument("--run", action="store_true", help="Run a bounded set of model calls after catalog verification")
args = parser.parse_args()
key = os.getenv("DEEPSEEK_API_KEY")
if not key:
    sys.exit("No DeepSeek key configured. Add it to your local .env; never to frontend code.")
client = OpenAI(api_key=key, base_url="https://api.deepseek.com", timeout=20, max_retries=0)
try:
    ids = sorted(m.id for m in client.models.list() if "deepseek" in m.id.lower())
except Exception as error:
    sys.exit(f"Catalog lookup failed ({type(error).__name__}); response body and credentials omitted.")
print(json.dumps({"deepseek_catalog_ids": ids}))
if not args.run:
    sys.exit(0)
selected = os.getenv("MODEL", "")
if selected not in ids:
    sys.exit("MODEL must exactly match one of the returned DeepSeek catalog IDs.")
os.environ["MOCK_LLM"] = "0"
os.environ["LANGSMITH_TRACING"] = "false"
import agents
cases = [("Unit 4B kitchen pipe is leaking", "maintenance"),
         ("Unit 8 towels are dirty and trash remains", "housekeeping"),
         ("What is the wifi password?", "guest_support")]
results = []
for message, category in cases:
    started = time.perf_counter()
    try:
        decision = agents.triage(message)
        results.append({"case": category, "pass": decision["category"] == category,
                        "latency_s": round(time.perf_counter()-started, 3)})
    except Exception as error:
        results.append({"case": category, "pass": False, "error_type": type(error).__name__})
inc = {"id":"synthetic-evidence-1", "message":"AC fails to cool", "category":"maintenance"}
for evidence, expected in [("Replaced sink P-trap. Photo attached.", False),
                           ("Measured AC discharge at 12 C after compressor repair; apartment cooled from 30 C to 23 C in one hour.", True)]:
    try:
        decision = agents.verify(inc, evidence)
        results.append({"case":"evidence-match" if expected else "unrelated-evidence", "pass":decision["complete"] == expected})
    except Exception as error:
        results.append({"case":"verification", "pass":False, "error_type":type(error).__name__})
print(json.dumps({"model":selected,"synthetic_only":True,"results":results}, indent=2))
sys.exit(0 if all(r["pass"] for r in results) else 1)
