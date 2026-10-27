# API audit — 26 October 2026

## Addendum — graph questions, 27 October 2026

Added a read-only Q&A endpoint using the installed LangChain provider APIs: `ChatOllama` from `langchain-ollama` 1.1.0, `ChatOpenAI` 1.7.2, `ChatAnthropic` 1.7.6 and `ChatGoogleGenerativeAI` 4.4.2. Constructors were checked against installed signatures and instantiated with dummy credentials without inference. The request uses `ainvoke` with system/human messages; plain JSON output is validated locally for cross-provider compatibility. Graphiti and Commander APIs are unchanged.

Q&A uses current [Ollama](https://docs.langchain.com/oss/python/integrations/chat/ollama), [OpenAI](https://docs.langchain.com/oss/python/integrations/chat/openai), [Anthropic](https://docs.langchain.com/oss/python/integrations/chat/anthropic) and Google provider adapters. Preset Claude and Gemini model IDs were checked against official model catalogs. There is no claim of completed hosted or local inference without actual keys or installed model weights. See [Graph questions](GRAPH_QUESTIONS.md) for the full contract, tests and limitations.

## Original implementation audit

The empty workspace had Python 3.9 as its shell default, Python 3.12.10 installed via Homebrew, Node 23.11.0, npm 10.9.2 and uv. Python 3.12 was selected because current Deep Agents requires Python ≥3.11. Docker was not installed in the execution environment; real database integration was verified against a temporary native Neo4j Community 5.26.0 instance instead.

Before implementation, the following current official documentation and upstream source were inspected:

- [Deep Agents subagents](https://docs.langchain.com/oss/python/deepagents/subagents) and [`create_deep_agent` reference](https://reference.langchain.com/python/deepagents/graph/create_deep_agent).
- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts).
- [LangSmith custom instrumentation](https://docs.langchain.com/langsmith/annotate-code).
- [Graphiti upstream implementation](https://github.com/getzep/graphiti/blob/main/graphiti_core/graphiti.py), [node implementation](https://github.com/getzep/graphiti/blob/main/graphiti_core/nodes.py), and [episode documentation](https://help.getzep.com/v2/graphiti/core-concepts/adding-episodes). The episode documentation URL is versioned; installed 0.31.2 source signatures were used as the definitive implementation reference.

PyPI/npm metadata was checked and the following core versions were resolved and installed successfully:

| Dependency | Version |
| --- | --- |
| deepagents | 0.7.32 |
| graphiti-core | 0.31.2 |
| langgraph | 1.2.17 |
| langsmith | 0.14.12 |
| langchain-openai | 1.7.2 |
| langchain (transitive) | 1.4.7 |
| langgraph-checkpoint-sqlite | 3.1.2 |
| fastapi / uvicorn | 0.162.0 / 0.54.0 |
| pydantic / pytest | 2.14.1 / 9.1.1 |
| neo4j Python driver | 6.4.1 |
| React / Vite / TypeScript | 19.3.1 / 8.3.5 / 7.0.2 |
| Cytoscape | 3.34.4 |

## Confirmed APIs

`create_deep_agent(model=..., tools=..., system_prompt=..., subagents=..., checkpointer=..., name=...)` returns a compiled LangGraph graph. Declarative subagents accept name, description, system_prompt, tools and model. The built-in delegation tool is `task`, with `subagent_type` and `description`; planning uses `write_todos` with typed todo items. The application tests invoke these real tools through the installed middleware.

LangGraph uses `interrupt(payload)` and re-invocation with `Command(resume=value)` under the **same** `configurable.thread_id`. `AsyncSqliteSaver.from_conn_string()` is an async context manager. The implementation intentionally places decision writes after the interrupt and makes pre-interrupt status emission idempotent on resume. It uses a custom approval tool with `interrupt()` rather than `interrupt_on` tool review, which is also available in Deep Agents. This is the requested current LangGraph HITL mechanism.

`Graphiti.add_episode(name, episode_body, source_description, reference_time, source=EpisodeType.json, group_id=..., uuid=...)` accepts serialized JSON, not a Python dict. Optional extraction calls this actual API and times out gracefully. Canonical episodes use `EpisodicNode.save(client.driver)` to avoid probabilistic extraction on the critical path. Structured entities/relationships use `EntityNode.save`, `EntityEdge.save`, and `EpisodicEdge.save`. Graphiti exact reads use its Neo4j driver's `execute_query`; agents are never given raw query access.

Graphiti 0.31.2's Pydantic model permits missing embeddings, but its Neo4j save queries invoke vector-property procedures with those values. Real integration exposed a null-vector error. The implementation provides a local `EmbedderClient` subclass and non-null 1024-dimensional feature-hash vectors for node/edge saves. Exact, provenance-scoped controlled queries are the retrieval mechanism; no claim of semantic retrieval quality is made. This change eliminates a remote embedding dependency and allows structured Graphiti persistence to work offline.

Current LangSmith supports `@traceable`, `get_current_run_tree()` and the tree's `get_url()`. `LANGSMITH_TRACING=true`, API key and project enable tracing. The harness uses run names/tags/metadata and automatically emits delegation/tool spans. Initial offline validation did not exercise cloud upload; the later integration update below records credentialed verification.

## Verification limits

Real Deep Agents delegation, actual LangGraph interrupt/resume, durable SQLite resume after engine reconstruction, Graphiti/Neo4j persistence and reads, historical supersession, and browser injection→approval were exercised. Initial offline validation did not exercise remote LLM inference, optional Graphiti LLM extraction or LangSmith cloud uploads. See the later integration update below for credentialed verification. The Compose file describes the same Neo4j version used in native integration testing; Docker itself was unavailable here. A Graphiti upstream Pydantic deprecation warning is present and does not affect the tests.


## LangSmith integration update (2026-10-27)

- `GET /api/observability` exposes regional endpoint, project, key-configured boolean, feedback configuration and current trace ID; no credentials.
- `GET /api/evaluation` includes LangSmith state, trace IDs and process-check results; JSON download remains available.
- `POST /api/evaluation/publish` is opt-in via `LANGSMITH_PUBLISH_FEEDBACK`; tracing-only configuration returns `trace_outputs_only` without attempting feedback writes.
- Real trace ingestion was accepted by the EU endpoint. The configured tracing key cannot read projects or write feedback, datasets or experiments; those operations remain disabled.
- The isolated local regression suite passes three cases with six graders each. `--upload` uses the actual LangSmith evaluation SDK but requires broader permissions and has not been validated with this tracing-only key.
- Unit tests explicitly disable tracing and force deterministic incident agents.

See [LangSmith setup](LANGSMITH_SETUP.md) for usage and capability limits.

Credentialed live verification: a real OpenAI `gpt-4.1-mini` workflow passed all ten process checks, including retry recovery and durable human interrupt/rejection resume, in isolated temporary databases. EU trace ingestion reported no errors. Remote trace readback and cloud feedback/Experiments were not verified because the key is tracing-only. The OpenAI message-name restriction was fixed by naming the agent `supply-chain-commander`.

## Model-routing update (2026-10-27)

`ModelRoutingMiddleware` uses the installed LangChain `AgentMiddleware.awrap_model_call` and `ModelRequest.override(model=...)` APIs in Commander and declarative specialist middleware. Routine calls use the configured cheap model, planner/critic use the thinking model, and sourcing escalates after critic rejection. GPT-5.4 reasoning plus function tools requires the Responses API in credentialed testing; `use_responses_api=True` and reasoning-compatible sampling parameters are set for reasoning models.

Each model call records selection reason, tier, model, reported usage, duration and outcome in state, the evidence report and LangSmith spans. The UI adds **Models** in Agent activity and a routing section in Run evidence. Once a revised plan is independently verified, the next Commander request is constrained to the human-approval request tool. Premature approval requests return corrective evidence; the underlying engine's feasibility and human-decision checks remain enforced. Per-incident recorded settings survive backend reconstruction; historical incidents are not assigned fabricated call histories.

Local validation: 40 Python tests passed (one optional Neo4j test skipped), the frontend build passed, and the dedicated read-only routing browser test passed. See [model routing](MODEL_ROUTING.md) for configuration and the judge walkthrough.

Credentialed routing verification passed: routine GPT-4.1 mini and reasoning GPT-5.4, sourcing escalation, provider usage, durable human pause and synthetic rejection resume, all ten process checks passing, no reported LangSmith ingestion errors. See the verified-run section in [model routing](MODEL_ROUTING.md).
