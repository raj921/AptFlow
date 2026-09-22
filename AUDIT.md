# Product and engineering audit — 22 September 2026

## Verdict

The product serves a clear purpose: an operator can capture an apartment issue,
see who is responsible, understand why automation stopped, and record a review.
The previous implementation was a visual prototype with several misleading
reliability claims. The revised implementation is a hardened **local pilot**.
It is not ready to operate real apartments without supervision.

## What the assessment exposed and changed

| Area | Previous behavior | Current behavior |
|---|---|---|
| Booking retry | Retry incremented the booking key; a lost response could create another job | Transport retries preserve one logical key; persistent simulated receiver deduplicates it |
| Concurrency | A global lock enclosed model and vendor calls | Short SQL transactions; up to four independent requests processed concurrently |
| Crash recovery | No ownership or recovery protocol | SQL claim with a 120-second lease; token-fenced commits; accepted-job replay tested |
| Retry policy | SDK-like broad catch and sleeps; shared booking/retry counter | Three transient attempts per stage, durable due time, jitter, separate retry count; SDK retries off |
| Model trust | Parsed arbitrary JSON and trusted types | Strict Pydantic schema, bounded confidence, explicit enums, permanent failure escalation |
| Dispatch | AC issues went to a plumber; confidence ignored | Deterministic trade eligibility, ID allowlist and confidence gate; guest support stays with operator |
| Emergencies | Critical requests could auto-resolve | Emergency signals and critical classifications stop for human handling before dispatch |
| Evidence | Plumbing text could close an AC request | Offline receipt must match the request; live verification instructed to reject unrelated evidence |
| Operator action | HTTP 200 even when action failed | Invalid inputs return 422; missing/stale review returns 409; review note required and recorded |
| Permissions | Unauthenticated API | Shared operator authentication supported on frontend and API; fail-closed production configuration |
| Submission | Duplicate retries created duplicate requests | Idempotency key persisted; same-key conflicting payload returns conflict |
| Availability | Fake worker activity and online labels | Worker heartbeat and API error state; stale data explicitly labeled |
| UX | Dead navigation/filter/appearance controls; stale detail drawer | Working navigation/filtering, refreshed details, per-request history, inline failures, disabled pending actions |
| Accessibility | Custom overlay without complete keyboard behavior | Installed Radix Dialog, titles/descriptions, focus trap, Escape, explicit focus restoration; labeled inputs |
| Honesty | Hard-coded evaluation score, vendor response promises, ambiguous live status | No invented score/SLA; model and simulated vendor modes labeled; guest replies are drafts |

## Design judgment

The dashboard should answer three questions quickly: what needs my attention,
what is currently assigned, and what supports closure? Its queue filters,
review reasons, vendor assignment and per-request trace now support those tasks.
The display uses plain language such as "Needs review" instead of exposing
internal orchestration terminology to the operator.

Designeer is a discovery directory, not proof that every linked component was
used. The browser was used to inspect its current links and the actual Radix
Dialog documentation. The app now imports Radix Dialog and Lucide React rather
than claiming those libraries merely because their homepages were visited.
Funnel typography and the editorial dark layout remain the visual reference.
CSS retains sliding selection, changed-count motion, loading placeholders,
button feedback, panels and toasts, with reduced-motion rules.

The resumed Ego Browser checks exercised the revised UI after restarting a stale
frontend process. Submission, emergency escalation before booking, required review
notes, keyboard manual closure, Escape and focus restoration passed. Search and
the resolved filter found the new request. At a 390px viewport, the document had
no horizontal overflow and the detail panel fit the viewport. Reduced-motion
emulation reduced the tab transition to effectively zero. Simulated network loss
displayed the service warning; restoring the connection cleared it automatically.
The final workflow step is labeled "Closeout" so manual closure does not imply
that evidence verification occurred.

An ordinary "room is dirty" request completed through housekeeping classification,
SparkClean assignment, matching simulated receipt and closure with one booking.
The demo classifier did not recognize "needs cleaning" and stopped for review;
this is a limitation of its explicit keyword fixtures, not evidence of live-model
quality. No live-model quality claim is made.

These are browser interaction and DOM checks, not a complete visual or accessibility
audit. Ego's screenshot capture timed out. One automated pointer click on the
review button reported interception; DOM hit-testing found the button and keyboard
submission succeeded. A subsequent pointer click passed after keyboard focus
brought the button into view. A fresh visual inspection remains unverified;
no screenshot of the revised UI is claimed.

The vendor directory then exposed a real layout defect: a long activity detail
made the first lower-grid column expand to roughly 2,500px, pushing the vendor
panel off-screen while leaving its DOM present. Adding `min-width: 0` to the
lower-grid items fixed the intrinsic-size overflow. Browser geometry and a focused
screenshot now show all three vendors in the desktop layout; the 390px layout
stacks the panels with no horizontal overflow.

## Evidence from this checkout

`python evals.py` ran **20/20 checks successfully**. The suite includes:

- Ordinary resolution, emergency escalation, uncertain classification and manual closure.
- Trade selection and guest-support routing; low-confidence and invented vendors blocked.
- Timeout after acceptance retains the same booking key; three-attempt failure budget.
- Backoff persists and prevents early retry; unrelated evidence never silently resolves.
- Invalid booleans and NaN confidence rejected; new human dispatch gets a new booking generation.
- Duplicate processing claims once; reads/writes remain available during a blocked decision.
- Stale worker cannot commit; a killed subprocess resumes after lease expiry with one booking.
- Concurrent identical ingress creates one incident; conflicting reuse rejected.
- API authentication, missing/stale actions, invalid input, cross-origin writes, and body-size limits.
- LangChain structured output through a local HTTP fixture; 429/503 retry classification,
  401 permanent failure, and no SDK retry multiplication.
- 100 synthetic requests with four worker slots resolved in **1.69 seconds** in the
  recorded run, with exactly 100 simulated jobs. This is local mock throughput,
  not a real-model, vendor, multi-host or network benchmark.

The production frontend build and TypeScript check passed. A separate production-server check returned 401 for missing/wrong operator credentials and 200 for valid credentials on both the page and API proxy. Python dependency
compatibility passed. The demo exercised both resolution and operator escalation.
The official DeepSeek API was checked with the supplied key: `/v1/models`
returned `deepseek-flash` and `deepseek-v4-pro`. Five bounded synthetic live
checks passed through `deepseek-flash` (three triage cases and two evidence
cases). Thinking mode was disabled for these short JSON decisions, the key is
stored only in the ignored owner-readable `.env`, and the browser never receives
it. These checks validate connectivity and schema behavior, not production model
quality. A separate live end-to-end request reached `live-model`, was triaged as
maintenance, dispatched twice using the stable logical workflow, and escalated
after DeepSeek correctly rejected the simulated completion as insufficient
evidence. That result is expected for this pilot because simulated evidence is
not proof of a physical repair.

A real HTTP check through the running Next.js proxy also passed: submission,
duplicate suppression, emergency escalation, per-request history, operator
resolution, and a 409 response for a stale repeated review. Browser interaction
checks are recorded above, with their remaining visual and pointer limitations.

## What production still needs

1. **Real-world integrations and evidence.** Replace the simulated vendor receiver
   and immediate simulated completion with signed callbacks tied to job and vendor.
   Add a transactional outbound delivery record and an idempotent external sender.
   A model saying "complete" cannot verify a photograph, a physical repair or actual delivery.
2. **Identity and tenancy.** Shared Basic authentication is a private-pilot control,
   not individual operator identity, property-level access, roles, revocation or an audit-grade identity trail.
   Require HTTPS before hosting it. Guest unit text is not a structured apartment/tenant model.
3. **Independent model evaluation.** Current fixtures verify mechanics. Add a reviewed,
   held-out set of real apartment cases, including multi-issue tickets, negation,
   ambiguous urgency, prompt injection, missing evidence and costly incorrect closure.
   Measure unsafe closure rate, escalation recall, category/trade accuracy, latency and cost.
   Human graders must verify labels; model confidence needs calibration.
4. **Operational ownership.** Define request-processing and recovery SLOs, alerts on
   stale heartbeat, retry exhaustion and oldest queued request, backups and restoration,
   incident retention/deletion, provider budgets and dependency vulnerability checks.
   There is a heartbeat but no deployed monitoring or paging.
5. **Scaling evidence.** SQLite WAL supports this single-host pilot. The four slots
   are per process; adding API worker processes multiplies provider concurrency.
   Network partitions, regional failover, shared filesystem semantics, provider
   quota contention and long-latency tails are untested. Move claims to Postgres
   only when measured capacity or availability requirements demand it.
6. **Product validation.** Test the complete revised flow with an actual apartment
   operator, on keyboard, mobile and screen reader. Large queues need server-side
   search and pagination; the current queue is deliberately bounded to recent requests.

## Model and skill provenance

DeepSeek's official model page identifies `deepseek-flash` as V4.1 Flash and
documents `https://api.deepseek.com` as the OpenAI-compatible endpoint.
`provider_check.py` lists IDs without printing the key and runs five synthetic
checks only when explicitly passed `--run`.
Keys belong in ignored `.env`, never the frontend. External tracing stays off.

Skill discovery was repeated with `npx skills find 'agent evaluation'`,
`npx skills find 'langgraph'`, and `npx skills find 'backend engineering'`.
Relevant official LangChain skills and the evaluation skills were already
installed. This pass reused Ponytail, Find Skills, ecosystem-primer,
langgraph-fundamentals (Python reference), langchain-dependencies, and the
LLM-evaluation guidance. The more elaborate Harbor benchmark skill was inspected
but is unnecessary for this bounded local fault-injection suite.

Primary references: [LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api),
[LangChain ChatOpenAI](https://docs.langchain.com/oss/python/integrations/chat/openai),
[DeepSeek models](https://api-docs.deepseek.com/quick_start/pricing/),
[DeepSeek JSON output](https://api-docs.deepseek.com/guides/json_mode/),
[DeepSeek thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/),
[Designeer](https://designeer.xyz/components),
[Radix Dialog](https://www.radix-ui.com/primitives/docs/components/dialog),
[Lucide React](https://lucide.dev/guide/react/).
