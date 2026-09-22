# Hyatus Ops

An apartment-operations pilot: accept a request, classify it, choose an eligible
vendor, review completion evidence, and stop for an operator when uncertain.
This is a working local workflow, **not a production apartment service**.

## What runs

- Next.js 16 App Router and React 19; Radix Dialog; Lucide icons; Funnel typography.
- FastAPI, SQLite WAL, LangGraph routing, and LangChain's OpenAI-compatible model adapter.
- Four independent incident slots by default; a single incident's dependent steps remain sequential.
- SQL claims, expiring leases, fenced commits, persistent retries, and a simulated idempotent vendor receiver.
- Explicit operator review for emergency signals, uncertain classifications, unsupported routing, invalid decisions, and exhausted retries.

The model proposes classifications and evidence decisions. Python validates the
schema and confidence; deterministic policy controls state transitions, vendor
eligibility, retry budgets, and human permissions. Self-reported confidence is
not calibrated probability and is insufficient to prove real-world correctness.

## Local run

Requires Python 3.12 and Node 20.9+.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm ci
cp .env.example .env
chmod 600 .env
.venv/bin/uvicorn main:app --env-file .env --host 127.0.0.1 --port 8000
```

In a second terminal, run `npm run dev` and open http://localhost:3000.
Next.js reads `.env`; the API reads it through `--env-file`. Keep the backend
bound to loopback when both services run on one host.

The default is `MOCK_LLM=1`. Vendor dispatch, completion evidence, and the
vendor directory are simulated in **both** modes. Guest updates are recorded
drafts, not messages delivered to guests. The banner makes these boundaries visible.

## Checks

```sh
.venv/bin/python evals.py
.venv/bin/python demo.py
npm run build
.venv/bin/pip check
npm audit --omit=dev
```

The offline suite uses isolated temporary databases and fault injection. It
checks crash-after-acceptance recovery, stable booking keys, duplicate ingress,
claim races, stale-worker fencing, retry exhaustion and persisted backoff,
invalid model output, wrong evidence, operator permissions and HTTP errors.
It also exercises LangChain's structured output and 429/503/401 behavior with
an HTTP fixture and drains 100 synthetic requests through four worker slots.
These checks do **not** establish live-model quality or distributed-service capacity.

## Official DeepSeek API / V4.1 Flash

DeepSeek's official API identifies `deepseek-flash` as DeepSeek-V4.1-Flash and
uses the OpenAI-compatible endpoint `https://api.deepseek.com`. The checked-in
configuration uses that exact model ID and endpoint; no gateway or guessed
provider prefix is involved.

1. Put `DEEPSEEK_API_KEY` in the local `.env`, never in browser code or a `NEXT_PUBLIC_` variable.
2. Run `.venv/bin/python provider_check.py` to verify the official catalog.
3. Run `.venv/bin/python provider_check.py --run` for five bounded synthetic checks.
4. Set `MOCK_LLM=0` and restart the API only after verifying the selected model.

`provider_check.py` prints model IDs, pass/fail, and timing; it omits keys,
provider error bodies, and prompts. Runtime logs omit exception bodies.
The model timeout is 20 seconds; SDK retries are disabled. The durable queue
retries transport errors, 429s, and 5xx responses at most three attempts per
stage. Authentication, malformed output, and other non-transient errors go to
review. External tracing is disabled by default; the local log still contains
request data and must be protected.

## Private pilot deployment boundary

Set `APP_ENV=production` and a random `OPERATOR_PASSWORD` of at least 16
characters in both services. The frontend and API both require HTTP Basic
credentials (`operator` plus that password). HTTPS termination is required.
Set `FRONTEND_ORIGIN` to the hosted origin. Missing backend credentials fail
startup; no key silently switches live mode to demo mode.

This is one operator role and one workspace, not tenant isolation or enterprise
identity. It still needs real vendor adapters, signed completion callbacks,
trusted evidence, guest delivery, an independent live-model evaluation set,
backups and restore exercises, alerting, retention policy, deployment checks,
and measured service-level objectives before real apartments depend on it.

SQLite is deliberately single-host. The claim and fence semantics are tested;
there is no claim of multi-region or exactly-once external execution. After a
crash, another worker can reclaim an incident after 120 seconds. A real vendor
must persist and deduplicate the booking key, including ambiguous timeouts.
Queue admission is bounded at 1,000 active requests. The UI shows the latest
100 requests, with aggregate counts and per-request history fetched separately.

See [AUDIT.md](AUDIT.md) for the before/after assessment and remaining gates.

## Sources actually consulted

- [Designeer component directory](https://designeer.xyz/components): discovery and visual inspiration.
- [Radix Dialog](https://www.radix-ui.com/primitives/docs/components/dialog): the installed dialog primitive.
- [Lucide React](https://lucide.dev/guide/react/): the installed icon package.
- [Transitions.dev](https://transitions.dev/): CSS motion references; reduced-motion support.
- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api): explicit routing and nodes.
- [LangChain ChatOpenAI](https://docs.langchain.com/oss/python/integrations/chat/openai): structured model output.
- [DeepSeek model details](https://api-docs.deepseek.com/quick_start/pricing/): V4.1 Flash naming and official endpoint.
- [DeepSeek JSON output](https://api-docs.deepseek.com/guides/json_mode/): JSON response requirements.
- [DeepSeek thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/): disabled thinking for short deterministic decisions.

The catalogs are references, not code copied wholesale. Only Radix and Lucide
are installed UI components; custom application code connects them to this workflow.
