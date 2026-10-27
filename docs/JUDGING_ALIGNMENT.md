# Hackathon judging alignment

This guide maps the application to the slides supplied in `IMG_5014.HEIC` and `IMG_5015.HEIC`. The judging slide lists **tool use and steps**, **reliability/recovery**, **creativity in the harness**, and a **working demo**. It gives no numeric weights. The other slide explicitly calls its ideas “starting points, not rules”; implementing every idea is unnecessary.

## Recommended positioning

Present this as an **evidence-driven supply-chain agent harness**: a policy event triggers investigation, a capacity check rejects a tempting plan, the workflow recovers and replans, an extra-cost limit checks the revision, and a human makes the final decision. The graph makes dependencies visible; the tool records show how the result was reached.

Lead with the failed plan and recovery. The projected €7,310 savings is the business outcome, while the tool sequence, independent constraint checks, temporal provenance and resumable approval are the technical contribution.

## Criteria mapped to visible evidence

| Criterion from slide | What to demonstrate | Where the evidence is |
| --- | --- | --- |
| Tool use and the steps it takes | Six specialists, actual function calls, input arguments, outputs and timings | **Agent activity → Tools**; **Run evidence → Actual tool inputs & outputs** |
| Reliability: recovery when things go wrong | Capacity rejection/replanning plus a separate transient read exception that succeeds on one retry | **Test recovery** before injection; Story shows RETRY/RECOVERED; report links the attempts by `call_id` |
| Creativity: pushing the harness | Curated temporal world model, controlled tools, independent Critic, sourcing-cost guard, checkpointed human decision and evidence-based process checks | Graph, before/current view, approval panel, run report and source files |
| Working demo | Complete injection → failure → revised plan → approval → recorded decision using installed dependencies | Dashboard; deterministic rehearsal; backend/browser regression checks |

The process checks are **not a judging score**. Each check is pass, pending or fail according to the current backend records. They do not grade creativity, model intelligence, or semantic answer quality. A valid source ID also does not prove an LLM answer is correct.

## What was adjusted

### Inspectable tool execution

`IncidentEngine.read_tool()` records `TOOL_START`, `TOOL_RESULT` and `TOOL_ERROR` events around actual read-only operations. Records include:

- The function name and specialist responsible.
- Input arguments, serialized outputs and measured operation time.
- A call ID linking the original read with its retry.
- Attempt number and exception type when a read fails.

The Story tab keeps the main narrative legible. The Tools tab exposes the underlying calls. The report exports both. Durations measure the business operation, not UI pacing, framework overhead or total wall time.

### Failure injection and bounded recovery

Checking **Test recovery** injects a one-time `ConnectionError` before the first sourcing read. The exception is real and the read wrapper executes the retry path; the failure itself is deliberately simulated, not an actual supplier outage. It retries the read once, keeps the same incident/thread ID, and continues after success.

Only explicitly read-only operations are retried. Fact/episode writes and approval decisions are not automatically replayed by this wrapper. Unexpected validation errors stop immediately. Instrumented read attempts are capped at 40 by default, and Deep Agents retains its recursion limit. Live specialist operations are serialized against shared incident state.

This is distinct from the existing first-plan rejection: the Critic rejects that plan because B needs 4,000 units but has only 2,000 available. One demonstrates business-constraint recovery; the other demonstrates runtime error recovery.

### Spending limit and human approval

**Extra-cost limit €** is a limit on modeled *incremental procurement cost over the pre-tariff baseline*, not an LLM/API token budget. The default is €6,000. The revised plan adds €4,990, so it can proceed to human approval.

At a €3,000 limit, the same feasible sourcing plan enters **POLICY BLOCKED**. No approval interrupt is offered and no approved decision is written. A budget pass does not itself approve the plan: the human interrupt remains mandatory. The prototype executes no purchase orders.

Limits are captured when the incident is injected and remain fixed for that run. Reset to try a different limit.

### Process report

Open **Run evidence** or call `GET /api/evaluation`. Download JSON with `GET /api/evaluation?download=true`.

The report checks actual tool results, capacity violations, event ordering, aggregate allocation capacity, spending enforcement, decision provenance, source episodes, bounded attempts and the real LangGraph interrupt. It reads current/recent checkpoint history to verify the pause. The report includes the execution mode and checkpoint backend, so an in-memory test checkpoint cannot be mistaken for durable SQLite persistence.

Checks that have not been exercised remain pending. For example, transient recovery is pending when Test recovery was off; the interrupt check is pending when a spending block correctly prevented reaching approval. There is no hard-coded success animation or LLM-as-judge score.

## Three-minute walkthrough

1. **0:00–0:20 — Frame the problem.** “A tariff changes one country/category, but commitments sit several dependencies away. We investigate and verify a response before authorizing it.” Show the graph and current model/memory mode.
2. **0:20–0:35 — Start the run.** Enable **Test recovery**, leave the extra-cost limit at €6,000, and click **Inject Tariff Event**.
3. **0:35–1:05 — Show steps.** Point to delegation and dependency highlights. Switch to **Tools**, open one input/output record, then return to Story. Show the one-time read error, bounded retry and recovery.
4. **1:05–1:35 — Reject and revise.** Show the Critic's 4,000-vs-2,000 capacity finding. Follow the revised multi-supplier strategy and independent validation.
5. **1:35–2:00 — Enforce authority.** Show €4,990 extra cost under the €6,000 limit, residual risk and **AWAITING HUMAN APPROVAL**. Approve; orders become mitigated and the decision is recorded.
6. **2:00–2:35 — Prove the path.** Open **Run evidence**. Show tool attempts, one retry, six specialists, the checkpoint backend/thread and process checks. Download the JSON; use LangSmith as additional evidence when configured.
7. **2:35–3:00 — Context and boundaries.** Toggle before/current or inspect provenance. If useful, ask the locally installed Mistral a short graph question. Finish with the business result and identify the current model/memory configuration honestly.

If a judge asks about safety, a separate run at a €3,000 limit demonstrates the spending block. Avoid adding that entire second run to a tight three-minute presentation.

## Important remaining gaps and how to describe them

### Commander model mode

The default Commander uses a **scripted `BaseChatModel` fixture inside the real Deep Agents harness**. Tools, subagent delegation, memory, guards and interrupts execute, but the fixture does not demonstrate autonomous LLM planning. The UI now labels this **SCRIPTED COMMANDER**, and the report states it explicitly.

The installed local Mistral powers **Ask graph**, not Commander. Do not describe those Q&A calls as proof of LLM-driven incident delegation. To demonstrate that, configure the supported live Commander path with a valid OpenAI key and rehearse it separately:

```dotenv
AGENT_MODE=live
OPENAI_API_KEY=your_key
OPENAI_MODEL=gpt-4.1-mini
```

Restart the backend after changing mode. A credentialed isolated OpenAI run passed all ten process checks, including failure recovery and human interrupt/rejection resume. Rehearse before presenting; live model calls take longer than the offline script. See [LangSmith setup](LANGSMITH_SETUP.md) for tracing and evaluations. The first/revised sourcing builders remain deterministic in either mode; the model delegates and explains, while Python calculates and enforces constraints. Mistral v0.2 is not configured as a native tool-calling Commander.

### Memory backend

When `MEMORY_MODE=local`, the dashboard uses a SQLite temporal fallback, not Neo4j/Graphiti. If showing Graphiti is part of the pitch, start Neo4j and use `MEMORY_MODE=graphiti`; verify the connected mode and rehearse before presenting. Do not claim the local label is a live Graphiti connection.

### Broader autonomy and other starting points

The planner is tailored to the supplied China/HS 8542 scenario. Arbitrary policy injection, general sourcing optimization, automatic model-cost routing and packaged Deep Agents skills are not implemented. Focused calculation tools are reusable, but they are not a configured Deep Agents skills package. The project uses Python for numeric tasks and a selectable local/hosted model for graph interpretation; that is a concrete division of work, not an autonomous model router.

These are suggestions for later development, not reasons to destabilize the working demo with untested downloads or a new inference stack immediately before judging.

## Validation

Relevant tests include `tests/test_run_controls.py`, `tests/test_agent.py`, `tests/test_api.py` and the frontend walkthroughs. They cover actual exception/retry recovery, spending rejection, attempt exhaustion, refusal to retry invalid operations, evidence based on records rather than completed-role claims, durable approval resume, and the original failed-plan/replan sequence.

```bash
uv run pytest -q
cd frontend
npm run build
npx playwright test
```

Use the [README](../README.md) for setup and the [architecture guide](ARCHITECTURE.md) for module boundaries. The UI/report additions do not enable ERP execution or change hosted API credential handling.


## Model routing demonstration

The **Models** tab shows actual model selections and outcomes: cheaper routine roles, thinking-model planner/critic, and sourcing escalation after rejection. **Run evidence** exports model names, policy, reasons, latency and provider-reported usage. LangSmith records routing spans around the actual model handler. This demonstrates task-based routing; measured cost savings and quality gains require comparison runs. See [model routing](MODEL_ROUTING.md) for configuration and the judge walkthrough.
