# hyatus-ops — autonomous apartment operations

An agent-driven ops loop that takes a raw guest message or sensor event and
carries it end to end: **triage → vendor dispatch → completion verification →
guest follow-up**, with retries, idempotency, evals, and a human-in-the-loop
escape hatch. This is a working slice of "apartments that run themselves."

## What the agents do

Three agents (DeepSeek via the Requesty gateway, OpenAI-compatible API), each
returning structured JSON **with a confidence score and reasoning** — the
engine acts on confidence, not vibes:

| Agent | Decision | When it defers |
|---|---|---|
| `triage` | category + severity of an incoming event | confidence < 0.6 → human |
| `dispatch` | which vendor gets the job | (retries on vendor reject) |
| `verify` | did the vendor's evidence actually fix it | vague evidence → re-dispatch, then human |

Every decision is written to an append-only event log (`triage`,
`dispatch_decision`, `evidence`, `verify`, `retry`, `escalated`, ...) so any
incident can be replayed and audited.

## The loop

```
NEW → TRIAGED → DISPATCHED → RESOLVED
          ↑          |
          +──────────┘  bad evidence → re-dispatch (max 2)
any state → ESCALATED (uncertain triage / retries exhausted / verify keeps failing)
ESCALATED → human clicks "resolve manually" or "send back to agents"
```

## Engineering decisions (the parts that actually matter)

- **Idempotent dispatch.** Every vendor job gets key `{incident}:dispatch:{attempt}`.
  A retry after a timeout can't double-book a plumber — the vendor dedupes on the key.
- **Retry with exponential backoff + jitter**, capped at 3 attempts, then
  escalate instead of looping forever. Backoff base is env-configurable so
  evals don't sleep.
- **Verification is a first-class step.** "Vendor says done" is not "done."
  Evidence is graded; weak evidence ("looked at it") sends the job back out.
- **Human-in-the-loop is a feature, not a failure.** Low-confidence triage,
  exhausted retries, and repeated verify failures all land in an ESCALATED
  queue a human can clear from the dashboard in one click.
- **Evals before vibes** (`evals.py`): scripted scenarios with graders —
  critical leak auto-resolves, vague message escalates, flaky vendor still
  completes via retry, bad evidence never blind-resolves. `4/4 passing`.
- **Boring infrastructure on purpose.** SQLite + one worker thread is the
  right size for a portfolio of apartments; the seams (queue, SKIP LOCKED,
  webhook callbacks) are marked where volume would force them.

## What broke along the way

1. **The mock triage matched "ac" inside "place"** ("quick question about the
   place" → dispatched an AC technician). Substring matching on keywords is a
   classification bug; fixed with word-boundary matching. This is exactly why
   evals exist — it was the eval, not a human, that caught it.
2. **First eval run "failed" the retry test** — not a code bug: the eval
   harness ran 60 sweeps in microseconds while real backoff waits seconds.
   Lesson: time is a dependency; made the backoff base injectable.
3. **LLM JSON isn't guaranteed parseable** — the client extracts the first
   `{...}` block and retries 3× before giving up, because a 2am malformed
   response shouldn't kill a worker. Unexpected agent errors escalate the
   incident instead of crashing the loop.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 evals.py          # 4/4 passing, fully offline
python3 demo.py           # prints a full incident transcript
uvicorn main:app          # dashboard at http://localhost:8000
```

Offline by default (deterministic mock agents). To run real DeepSeek through
the Requesty gateway:

```bash
export REQUESTY_API_KEY=...            # from https://app.requesty.ai
export MODEL=deepseek/deepseek-v3.1    # any model the gateway routes
unset MOCK_LLM
```

## Known ceilings (and the upgrade I'd take)

- Single worker thread → SQS or Postgres `SKIP LOCKED` when one sweep/500ms isn't enough.
- Simulated vendor callbacks → real webhook ingestion with signature verification.
- Keyword mock agents → the LLM path is already wired; next step is golden-set
  evals of triage accuracy against historical tickets, per model, before swapping models.
