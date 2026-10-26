# API audit — 26 October 2026

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

Current LangSmith supports `@traceable`, `get_current_run_tree()` and the tree's `get_url()`. `LANGSMITH_TRACING=true`, API key and project enable tracing. The harness uses run names/tags/metadata and automatically emits delegation/tool spans. Online trace upload was not tested without credentials.

## Verification limits

Real Deep Agents delegation, actual LangGraph interrupt/resume, durable SQLite resume after engine reconstruction, Graphiti/Neo4j persistence and reads, historical supersession, and browser injection→approval were exercised. Optional remote LLM inference, optional Graphiti LLM extraction, and LangSmith cloud uploads require credentials and were not exercised. The Compose file describes the same Neo4j version used in native integration testing; Docker itself was unavailable here. A Graphiti upstream Pydantic deprecation warning is present and does not affect the tests.
