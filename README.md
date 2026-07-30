# Enterprise Agents on Microsoft Foundry

A progressive, notebook-first learning laboratory for modern enterprise agents on
Microsoft Foundry.

The repository modernizes a brownfield LangGraph Text-to-SQL project one concept
at a time. Each release adds a single idea and leaves a readable checkpoint
behind, covering modern LangGraph architecture, models and tools, Foundry hosted
agents, Foundry IQ, memory, observability and evaluation, performance and
reliability, token economics, security and governance, operational control,
distribution, and external-hosting comparisons.

The Text-to-SQL agent stays deliberately understandable throughout. The subject
is the platform and the architecture around the agent, not agent complexity.

## Current release

`v0.3: Modern LangGraph Text-to-SQL Agent`

v0.3 migrates the Text-to-SQL agent onto LangGraph as an explicit `StateGraph`
with typed state, deterministic read-only validation before any execution, one
bounded repair attempt, and structured success or failure output. The model has
no tools and makes no routing decision. See
[docs/releases/v0.3.md](docs/releases/v0.3.md).

## Prerequisites

Python 3.12 or later, [uv](https://docs.astral.sh/uv/), Git, the
[Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli), the
[Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd),
and the Bicep CLI extension.

An Azure subscription with permission to create resource groups and role
assignments is required for provisioning. Nothing in the test suite needs Azure.

Running the database queries additionally needs the
[Microsoft ODBC Driver 18 for SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server),
which is a system package rather than a Python wheel. On Windows:
`winget install --id Microsoft.msodbcsql18`.

Verify everything with:

```console
uv run eaof-verify
```

## Commands

Two commands are installed by `uv sync`.

| Command | Purpose |
| --- | --- |
| `uv run eaof-verify` | Report whether this workstation and Azure environment are ready |
| `uv run eaof-db-info` | Inspect the configured database, read-only |

Both return `0` when the check passed, `1` when it ran and something failed, and
`2` when it could not run at all. The last distinction matters: a missing ODBC
driver and a broken database are different problems.

## Getting started

```console
uv sync
copy .env.example .env
```

Then open the notebooks in order.
[00_brownfield_baseline_and_azure_bootstrap.ipynb](notebooks/00_brownfield_baseline_and_azure_bootstrap.ipynb)
covers the baseline analysis and the Azure foundation.
[01_modern_python_foundation.ipynb](notebooks/01_modern_python_foundation.ipynb)
covers the package layout, typed configuration and errors, the database client,
the commands, and the test split.
[01.1_explore_adventureworks.ipynb](notebooks/01.1_explore_adventureworks.ipynb)
is an optional tour of the sample database: schemas, tables, keys, a handful of
beginner queries, and how that schema becomes agent context.
[02_modern_langgraph_text_to_sql.ipynb](notebooks/02_modern_langgraph_text_to_sql.ipynb)
covers the agent: graph state, topology, repair, unsafe-write rejection, and
structured output. It runs offline, and its live cell skips with a stated reason
when Azure is unavailable.

## Provisioning Azure

Nothing is created until the pre-provision check passes. It prints the topology,
the region, and every billable resource before anything happens.

```console
azd auth login
azd env new dev
uv run python scripts/preprovision_check.py
azd provision
uv run eaof-verify
uv run --extra database python scripts/bootstrap_database.py
```

Tear down with `azd down --purge`.

The full topology, naming scheme, identity model, schema verification evidence,
and cost summary are in
[docs/architecture/v0.1-azure-foundation.md](docs/architecture/v0.1-azure-foundation.md).

## Repository layout

| Path | Contents |
| --- | --- |
| `database/queries/` | Read-only inventory and smoke queries |
| `database/seeds/adventureworks-lt/` | Seeding fallback and the read-only grant script |
| `docs/adr/` | Architecture decision records |
| `docs/architecture/` | Target architecture per release |
| `docs/current-state/` | Analysis of the brownfield backend |
| `docs/releases/` | Release notes and measurements |
| `evals/datasets/` | Evaluation cases |
| `infra/` | Subscription-scoped Bicep and azd parameters |
| `notebooks/` | Progressive lessons |
| `scripts/` | Thin adapters: provisioning hooks and database bootstrap |
| `src/enterprise_agents_on_foundry/` | The package: `config`, `infrastructure`, `database`, `observability`, `cli`, `agents` |
| `tests/unit/` | Offline tests, no Azure access required |
| `tests/integration/` | Azure tests, marked `azure` |

## Quality gate

```console
uv sync
uv run pytest -m "not azure"
uv run pytest -m azure
uv run ruff check .
uv run ruff format --check .
uv run mypy src scripts
az bicep build --file infra/main.bicep
```

The offline suite needs nothing but Python and is the one that runs on every
change. The Azure suite needs a provisioned environment and skips with a stated
reason when one is absent.

## Safety defaults

Authentication is Microsoft Entra only. No key, password, or connection string is
accepted anywhere in the configuration model, and a test asserts that no setting
name contains password, secret, or key.

Database scripts refuse to modify anything unless `ALLOW_DATABASE_BOOTSTRAP=true`
is set explicitly, and they print the resolved target before acting.

Generated SQL is constrained by two independent controls: a database principal
holding `db_datareader` with writes explicitly denied, and a validator that
accepts exactly one `SELECT` or `WITH` statement.

Every optional capability defaults to off.

## Reference project

The brownfield project this repository modernizes lives at
`../langgraph-agents-on-azure`. It is treated as read-only and is never modified.
Every claim about it in the documentation cites a specific path under
`../langgraph-agents-on-azure/backend`.

## Roadmap

[docs/roadmap.md](docs/roadmap.md)
