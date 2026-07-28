---
title: Legacy Dependency Inventory
description: Analysis of the 31 pinned packages in the brownfield requirements.txt, including unused entries, beta versions, a missing declaration, and the replacement dependency model.
author: shiva
ms.date: 2026-07-27
ms.topic: reference
keywords:
  - dependencies
  - requirements
  - uv
estimated_reading_time: 7
---

## Source

`../langgraph-agents-on-azure/backend/requirements.txt`, 31 packages, every one
pinned with `==`, inspected 2026-07-27.

## Full list

| Package | Pinned version | Assessment |
| --- | --- | --- |
| aiofiles | 24.1.0 | Not imported anywhere in the backend |
| aiosqlite | 0.20.0 | Used by the checkpointer, replaced with Azure SQL |
| azure-ai-evaluation | 1.10.0 | Used by `evaluations/evaluation.py` |
| azure-core-tracing-opentelemetry | 1.0.0b11 | Beta pinned into production |
| azure-identity | 1.20.0 | Retained, upgraded |
| azure-monitor-opentelemetry-exporter | 1.0.0b33 | Beta pinned into production |
| azure-search-documents | 11.5.2 | Not imported anywhere in the backend |
| fastapi | 0.115.8 | Used by the service layer |
| ipython | 8.12.3 | Development tool declared as a runtime dependency |
| langchain-community | 0.3.18 | Supplies `SQLDatabase` and the toolkit |
| langchain-core | 0.3.37 | Core message and tool types |
| langchain-openai | 0.2.4 | Supplies `AzureChatOpenAI` |
| langgraph | 0.2.74 | Superseded by the 1.x line |
| langgraph-checkpoint-postgres | 2.0.15 | Second, unused persistence path |
| langgraph-checkpoint-sqlite | 2.0.5 | Ephemeral, file-based persistence |
| langsmith | 0.3.10 | Third-party tracing alongside Azure Monitor |
| nbformat | 5.10.4 | Notebook tooling declared as runtime |
| nltk | 3.9.1 | Not imported anywhere in the backend |
| opentelemetry-instrumentation-langchain | 0.38.7 | Traceloop instrumentation |
| opentelemetry-sdk | 1.30.0 | Tracing core |
| pandas | 2.2.3 | Used by evaluation and notebooks |
| plotly | 6.0.0 | Used only by the unreferenced visualization agent |
| prompty | 0.1.48 | Tracer used by `agents/tracing.py` |
| psycopg | 3.2.5 | PostgreSQL path, unused at runtime |
| psycopg-binary | 3.2.5 | Same |
| psycopg-pool | 3.2.6 | Same |
| pydantic | 2.10.6 | Retained, upgraded |
| pyodbc | 5.2.0 | Not imported anywhere in the backend |
| python-dotenv | 1.0.1 | Retained |
| tavily-python | 0.5.1 | Not imported anywhere in the backend |
| uvicorn | 0.34.0 | Used by `main.py` |

## Findings

Five packages are declared and never imported: `tavily-python`, `nltk`,
`azure-search-documents`, `aiofiles`, and `pyodbc`. They enlarge the image,
widen the supply-chain surface, and imply capabilities the code does not have.

One package is imported and never declared. `api/settings.py` subclasses
`BaseSettings` from `pydantic-settings`, which does not appear in
`requirements.txt`. The service works only because a transitive dependency
happens to install it, which is an unpinned dependency in practice.

Two beta packages are pinned as though stable:
`azure-core-tracing-opentelemetry==1.0.0b11` and
`azure-monitor-opentelemetry-exporter==1.0.0b33`. Neither offers compatibility
guarantees.

Three tools intended for development are declared as runtime dependencies:
`ipython`, `nbformat`, and to a lesser degree `pandas`. Because there are no
dependency groups, the container image carries all of them.

Two persistence stacks ship at once. Both the SQLite and the PostgreSQL
checkpointer packages are installed, but only one code path executes and the
PostgreSQL branch is commented out in `api/service.py`.

Two tracing systems ship at once. `langsmith` and the Azure Monitor exporter are
both present, and the Azure Monitor path is inactive because of the environment
variable mismatch described in [backend-inventory.md](backend-inventory.md).

Pinning is not locking. Every direct dependency has an exact version, but no
transitive dependency does, and there are no hashes. Two installations of the
same `requirements.txt` a month apart can resolve differently, and neither can
be verified.

## Replacement model

The modernized repository uses `uv` with `pyproject.toml` and a committed
`uv.lock`.

Direct dependencies are declared as compatible ranges rather than exact pins, so
patch-level security fixes are available without editing the manifest. The exact
resolved graph, including every transitive package, is recorded in `uv.lock` and
committed. Reproducibility comes from the lockfile, not from the manifest.

Development tooling lives in the `dev` dependency group, so it is installed for
contributors and excluded from any runtime install.

Native dependencies are optional. `pyodbc` requires the Microsoft ODBC Driver
18 for SQL Server, a system package rather than a wheel, so it sits in the
`database` extra. Cloning the repository and running the tests never requires a
native driver.

The v0.1 runtime footprint is four packages: `azure-identity`, `pydantic`,
`pydantic-settings`, and `python-dotenv`. LangGraph, FastAPI, and the
observability stack arrive in later releases, when there is code that uses them.

## Related

- [backend-inventory.md](backend-inventory.md)
- [modernization-risks.md](modernization-risks.md)
