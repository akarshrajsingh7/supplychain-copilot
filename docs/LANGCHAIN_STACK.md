# LangChain products and integrations used here

The application uses **Deep Agents** for orchestration, **LangChain** interfaces for models and tools, **LangGraph** for execution/checkpointing/human approval, and **LangSmith** for optional tracing. Graphiti, Neo4j, FastAPI and the React UI are separate technologies.

This inventory describes the checked-in implementation and locked dependencies. See [Architecture](ARCHITECTURE.md) for the complete flow and [API audit](API_AUDIT.md) for the original compatibility checks.

## 1. Product-to-code map

| Product / package | Version in `uv.lock` | Role in this application | Main code |
| --- | --- | --- | --- |
| Deep Agents / `deepagents` | 0.7.32 | Commander harness; planning; `task` delegation to six declarative subagents | [`commander.py`](../backend/agents/commander.py) |
| LangChain / `langchain` | 1.4.7, transitive | Underlying agent interfaces and middleware used by Deep Agents | Used through the Deep Agents runtime |
| LangChain Core / `langchain-core` | 1.7.0, transitive | `@tool`, `BaseChatModel`, `AIMessage`, `ChatGeneration`, `ChatResult` | [`commander.py`](../backend/agents/commander.py), [`deterministic_model.py`](../backend/agents/deterministic_model.py) |
| OpenAI integration / `langchain-openai` | 1.7.2 | `ChatOpenAI` for live-mode Commander and specialists | [`commander.py`](../backend/agents/commander.py) |
| LangGraph / `langgraph` | 1.2.17 | Compiled agent execution; thread-scoped `interrupt` and `Command` resume | [`engine.py`](../backend/engine.py) |
| LangGraph SQLite checkpoint / `langgraph-checkpoint-sqlite` | 3.1.2 | `AsyncSqliteSaver` for persistent checkpoints | [`api/main.py`](../backend/api/main.py) |
| LangSmith / `langsmith` | 0.14.12 | Decorated tool/investigation traces, framework spans, optional trace URL | [`engine.py`](../backend/engine.py), [`supply_tools.py`](../backend/tools/supply_tools.py) |

The transitive `langgraph-checkpoint`, `langgraph-prebuilt`, `langgraph-sdk` and `langchain-protocol` packages also appear in the lockfile. Their installation does not imply that the application uses a hosted Agent Server, SDK client deployment, or another protocol integration.

## 2. Deep Agents: the orchestration harness

`build_commander()` calls `create_deep_agent(...)` with a Commander model, system instructions, six subagent specifications, the approval tool, a checkpointer, and a run name. This is the framework's actual harness. Deep Agents supports planning and subagent delegation; this application exercises `write_todos` and `task`. See the official [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview) and [subagent documentation](https://docs.langchain.com/oss/python/deepagents/subagents).

The Commander delegates to Policy, Impact, Inventory, Sourcing, Mitigation Planner and Risk / Critic roles. The roles use the same model selection, but have different instructions and narrow tools. They are logical agents in one backend process, not six independently hosted services.

In deterministic mode, the *decision to call the next tool* is scripted. The harness still executes the actual delegations and tools. It does not represent autonomous LLM reasoning or discovery of an unknown recovery path. In live mode the language model chooses calls under the Commander instructions, while Python still computes all numeric results and enforces the final gate.

The harness includes capabilities beyond those needed here. Filesystem tools are not part of the supply-chain product workflow; the Commander prompt instructs the model not to use them. There is no custom filesystem-permission enforcement added by this project.

## 3. LangChain: tool and model interfaces

The `@tool` decorator converts specialist wrappers and the approval function into tools callable by the agent runtime. Each specialist wrapper delegates to `engine.investigate(role_id)`; the resulting dictionaries contain deterministic tool evidence. These are controlled business operations, not an unrestricted Python or Cypher console.

The offline model subclasses `BaseChatModel` and emits `AIMessage` tool calls, packaged in `ChatResult` / `ChatGeneration`. This is a test/demo adapter for the same runtime interfaces used by real chat models.

Live mode constructs `ChatOpenAI(model=OPENAI_MODEL, temperature=0)`. The default configured model name is `gpt-4.1-mini`; it is configurable, not a claim about which model is best or currently available to an account. Live operation needs an OpenAI API key and network access. See the official [ChatOpenAI integration](https://docs.langchain.com/oss/python/integrations/chat/openai).

The project does not build a separate document-RAG pipeline with LangChain loaders, splitters or vector stores. Its business retrieval is through the Graphiti adapter and controlled supply tools.

## 4. LangGraph: execution and human approval

Deep Agents returns a compiled LangGraph agent. The application invokes it with `ainvoke` and a stable `configurable.thread_id` derived from the incident run ID. No separate hand-written `StateGraph` topology is constructed by this project.

The approval tool calls `interrupt({plan, evaluation, allowed})`. LangGraph pauses at that point using the configured checkpointer. After the operator's choice, the backend invokes the same agent and thread with `Command(resume="APPROVE")` or `"REJECT"`. This follows the documented [LangGraph interrupt/resume mechanism](https://docs.langchain.com/oss/python/langgraph/interrupts).

The application uses `AsyncSqliteSaver`, not just an in-memory checkpointer, so a pending approval can survive backend reconstruction. Tests also use `InMemorySaver` for isolated agent checks. Business memory and checkpoints have different jobs: Graphiti knows the supply-chain facts; the checkpoint knows where agent execution paused.

This implementation uses an explicit `interrupt()` inside its approval tool. It does **not** configure Deep Agents `interrupt_on` to review arbitrary tool calls, although that is a separate available framework feature. Approval writes occur after resumption, and repeated pre-interrupt UI emission is guarded.

## 5. LangSmith: observability

The code decorates the investigation, downstream traversal, plan simulation and decision resume with `@traceable`. The framework also supplies nested agent/model/tool traces. Configuration adds run names, simulation/model-mode tags, incident ID and `demo_run` metadata. When tracing is enabled, `get_current_run_tree().get_url()` supplies the header's trace link. Official configuration is described in [LangSmith custom instrumentation](https://docs.langchain.com/langsmith/annotate-code).

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your_key
LANGSMITH_PROJECT=supplychain-copilot
```

An investigation trace is expected to show the Commander, delegations, specialist tools, failed capacity check, revised plan and successful verification. Human resume is another invocation sharing the incident/thread metadata; the code does not keep one continuously running cloud span open while waiting for a person.

LangSmith tracing can be enabled with the deterministic model as well as the live model. Trace upload needs credentials and internet; it is disabled for the offline path. The application does not currently create LangSmith datasets, run evaluation experiments, use prompt management, or deploy through LangSmith. Cloud uploads have not been verified without user credentials.

## 6. What is separate from LangChain

| Technology | Its role | Boundary |
| --- | --- | --- |
| Graphiti | Temporal entities, episodes, facts, supersession and provenance | Zep's framework, not a LangChain product |
| Neo4j | Graph database backing Graphiti mode | Database, not the agent runtime |
| Local feature-hash embedder | Non-null vectors for Graphiti node/edge storage | Custom Python; not OpenAI semantic embeddings or a LangChain vector store |
| FastAPI / Uvicorn | REST, SSE and HTTP serving | Application transport |
| SQLite | Agent checkpoints and temporal journal, in separate databases | Local persistence |
| React / TypeScript / Cytoscape | Dashboard, interaction and graph visualization | Client UI, not a LangChain-hosted frontend |
| Pydantic | Entity, event, allocation and fact schemas | Validation of application data |
| OpenAI | Remote inference in live mode; optional Graphiti extraction clients | Model provider, separate from LangChain |

Graphiti constructs its own OpenAI client/reranker adapters. Those are independent of `langchain_openai.ChatOpenAI`. In the default structured Graphiti path, local vectors and exact queries avoid remote embedding/reranking calls. Optional `GRAPHITI_EXTRACT=true` activates Graphiti's extraction API and writes candidates to the separate extraction namespace.

## 7. Configuration matrix

| Configuration | Deep Agents | Remote chat inference | Graphiti/Neo4j | LangGraph checkpoint | Cloud trace upload |
| --- | --- | --- | --- | --- | --- |
| Deterministic + local + tracing off | Yes | No | No | SQLite | No |
| Deterministic + Graphiti + tracing off | Yes | No, when optional extraction is off | Yes | SQLite | No |
| Live + local + tracing off | Yes | Yes | No | SQLite | No |
| Live + Graphiti + tracing on | Yes | Yes | Yes | SQLite | Yes, with LangSmith credentials |

Agent mode, memory mode and tracing are independent configuration choices. Optional extraction is a fourth choice. Installing provider packages such as Anthropic or Google integrations transitively does not mean they are used by this application; `build_commander()` selects only the scripted model or `ChatOpenAI`.

## 8. How to describe the demo accurately

> SupplyChain Copilot uses the Deep Agents harness to delegate a tariff investigation to six specialists. Python tools calculate exposure and validate recovery plans. Graphiti stores temporal business evidence in Neo4j, LangGraph checkpoints a human approval interrupt, and LangSmith can trace the execution. The repeatable offline mode uses a scripted tool-calling model; live mode uses a remote language model with the same business tools and approval gate.

The scripted recovery path is deliberately designed for the supplied fixture. The real framework behavior is delegation, tool execution, persistence and interrupt/resume; the numeric results come from Python. Arbitrary-country planning, semantic graph search, hosted agent deployment and automated procurement execution are not implemented.
