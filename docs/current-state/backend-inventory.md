---
title: Brownfield Backend Inventory
description: File-level inventory of the legacy LangGraph backend, recording what each component does and whether it is reusable, replaceable, or obsolete for the modernized repository.
author: shiva
ms.date: 2026-07-27
ms.topic: reference
keywords:
  - brownfield
  - langgraph
  - inventory
estimated_reading_time: 12
---

## Scope

Every path below is relative to `../langgraph-agents-on-azure/backend` on branch
`shiva-cicd-branch`, inspected read-only on 2026-07-27. No file in that
repository was modified.

## Entry point and packaging

`main.py` is eight lines. It calls `uvicorn.run(app, host="0.0.0.0", port=80)`
with the development port commented out, so switching between local and
container execution means editing source.

`Dockerfile` builds from `python:3.12.3-slim`, runs `COPY . /app`, installs from
`requirements.txt`, and ends with `CMD ["python", "main.py"]`. There is no
multi-stage build, no non-root user, and no lockfile. Because `COPY . /app`
copies the entire directory, the resulting image contains `chinook.db` and the
committed checkpointer state files.

`requirements.txt` pins 31 packages exactly, with no hashes, no lockfile, and no
separation between runtime and development dependencies. The consequences are
covered in [dependency-inventory.md](dependency-inventory.md).

## Agent implementation

`agents/sql_agent/sql_agent.py` (12.7 KB) defines `AzureSqlAgent`, which
hand-wires a `StateGraph(MessagesState)` with seven nodes:

```text
list_tables_ai_call -> list_tables_tool -> get_relevant_schema -> get_schema_tool
  -> query_agent -> tool_check -> (query_db -> execute_query) | generate_answer
  -> delete_intermediate_messages -> rolling_window -> END
```

Two details matter for the modernization. The graph fabricates a tool call with
the literal identifier `"tool_abcd123"` to force the first tool invocation,
which is a workaround for an API shape that LangGraph has since replaced.
Context is trimmed by emitting `RemoveMessage` objects against a hardcoded
`n_msgs=6` window, so conversation length is governed by a magic number rather
than a token budget.

The same file also contains `get_postgres_db_uri()`, reading `PG_VECTOR_*`
variables, and a `main()` demonstration that builds an `AsyncSqliteSaver`. The
module therefore mixes graph definition, connection-string assembly, and a
runnable demo.

`agents/sql_agent/sql_agent_postgres.py` (13.5 KB) is a near-duplicate of the
SQLite version. Two copies of the same graph diverging by 0.8 KB is the clearest
single indicator of copy-paste drift in the repository.

`agents/sql_agent/tools.py` instantiates `AzureChatOpenAI` and
`SQLDatabase.from_uri("sqlite:///chinook.db")` at module import time. Importing
the module therefore requires credentials to be present and requires the process
working directory to contain `chinook.db`. Tests, notebooks, and the API each
have to arrange their working directory to match.

`agents/sql_agent/prompts.py` holds `QUERY_AGENT_SYSTEM_MSG`, which instructs the
model to write SQLite syntax, cap results at 50 rows, and avoid DML. The
restriction exists only as prompt text. Nothing validates the generated SQL and
nothing restricts the database principal, so the safety property depends
entirely on model compliance.

`agents/visualization_agent/` contains a single-node graph using
`with_structured_output(PlotlyChart)` plus a 5.7 KB `chart_schema.py`. It is not
imported by the FastAPI service, so it is dead code at runtime.

## Service layer

`api/service.py` exposes `GET /health`, `POST /sql-invoke`, and
`POST /sql-stream` behind a FastAPI `lifespan` handler. It contains a defect
worth preserving as a teaching example:

```python
async with aiosqlite.connect(db_path) as conn:
    checkpointer = AsyncSqliteSaver(conn=conn)
```

The agent is constructed outside this block, because the construction lines sit
inside the indentation of a commented-out PostgreSQL branch. The connection is
therefore closed before the checkpointer is ever used.

Three further issues appear in the same file. `CORSMiddleware` is configured
with `allow_origins=["*"]` together with `allow_credentials=True`, a combination
browsers reject and which signals that origins were never considered. A debug
`print()` writes the full request keyword arguments, including user input, to
stdout. `HTTPException(detail=str(e))` returns raw exception text to the caller.

`api/settings.py` declares `AppSettings(BaseSettings)` with defaults supplied by
`os.environ.get(...)`. Those calls evaluate once, when the class body executes,
so a variable set after import is never seen, and an unset variable yields
`None` in a field declared as `str`.

`api/schema.py` is the strongest asset in the service layer. `UserInput`,
`StreamInput`, `AgentResponse`, and `ChatMessage` form a clean boundary, and
`ChatMessage.from_langchain` / `to_langchain` keep framework types out of the
public contract.

## Persistence

`agents/checkpointer.py` provides `get_sqlite_checkpointer()` and
`get_async_sqlite_checkpointer()`. The async variant returns a saver whose
connection has already been closed, and both swallow exceptions and return
`None`, converting a configuration error into a confusing downstream failure.

Three SQLite state files are committed: `agents/checkpointer_db/state.db`
(308 KB), `api/checkpointer_db/state.db` (2,296 KB), and
`notebooks/checkpointer_db/state.db` (2,508 KB). These are three divergent
snapshots of real conversation state in version control. `chinook.db` (892 KB)
is committed twice, at `backend/` and `backend/notebooks/`.

## Observability

`agents/tracing.py` wires the Prompty `Tracer` to an OpenTelemetry
`TracerProvider` with `AzureMonitorTraceExporter`, sampling at
`ParentBasedTraceIdRatio(1.0)`, and adds a custom `MarkdownExporter` plus a
`root_span` context manager.

It reads `os.getenv("APPINSIGHTS_CONNECTIONSTRING")`, but `.env.example` defines
`APPLICATIONINSIGHTS_CONNECTION_STRING`. The names never match, so Azure Monitor
export is silently inactive. `init_tracing(local_tracing=True)` returns `None`,
and the file is duplicated verbatim at `notebooks/tracing.py`.

## Evaluation

`evaluations/evaluation.py` runs `RelevanceEvaluator` and `SimilarityEvaluator`
against a hardcoded public address, `api_url = "http://52.248.105.188:80"`. The
address is a cluster IP that no longer resolves to anything meaningful, the
script reads `./data/...` relative to the working directory, and it is top-level
code with no functions, so nothing in it can be imported or tested.

`evaluations/data/evaluation_input.json` holds five question and ground-truth
pairs over Chinook, with prose answers such as "Iron Maiden, with total sales of
$138.6". The shape is reusable. The content is not, because the dataset changes.

## Deployment

`deployment-guide.md` describes a manual sequence: `docker build`,
`az acr login`, `docker push`, then Azure Web App or AKS. Secrets are passed as
`-e` flags on the command line. `.env.example` includes `AZURE_OPENAI_API_KEY`
and `PG_VECTOR_PASSWORD`, so the credential model is key and password based with
no Key Vault involvement.

## Disposition

Reusable in the new repository:

- The `api/schema.py` message boundary, including the LangChain conversion pair.
- The root-span tracing concept, once the environment variable name is fixed.
- The progressive notebook teaching format.
- The evaluation dataset shape, retargeted to AdventureWorksLT.
- The endpoint shape of `/health`, invoke, and stream.

Obsolete, and deliberately not carried forward:

- The LangGraph 0.2 hand-wired graph and the fabricated tool call identifier.
- Module-level model and database instantiation.
- The embedded SQLite Chinook database and every committed `.db` file.
- Dual SQLite and PostgreSQL checkpointers, and the duplicated PostgreSQL agent.
- API key authentication and password-bearing environment contracts.
- `requirements.txt` as the dependency mechanism.
- `allow_origins=["*"]` with credentials enabled.
- Hardcoded public IP addresses in evaluation code.
- Manual Docker, ACR, Web App, and AKS steps as the initial deployment model.

## Related

- [current-architecture.md](current-architecture.md)
- [dependency-inventory.md](dependency-inventory.md)
- [modernization-risks.md](modernization-risks.md)
