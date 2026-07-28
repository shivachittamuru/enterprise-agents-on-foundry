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

`v0.1: Brownfield Baseline, Repository Bootstrap, and Azure Foundation`

v0.1 analyses the legacy backend, establishes the modern Python baseline, and
provisions the Azure foundation. It builds no agent and migrates no LangGraph
code. See [docs/releases/v0.1.md](docs/releases/v0.1.md).

## Prerequisites

Python 3.12 or later, [uv](https://docs.astral.sh/uv/), Git, the
[Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli), the
[Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd),
and the Bicep CLI extension.

An Azure subscription with permission to create resource groups and role
assignments is required for provisioning. Nothing in the test suite needs Azure.

Running the database queries additionally needs the
[Microsoft ODBC Driver 18 for SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server),
which is a system package rather than a Python wheel.

Verify everything with:

```console
uv run python scripts/preprovision_check.py
```

## Getting started

```console
uv sync
copy .env.example .env
```

Then open
[notebooks/00_brownfield_baseline_and_azure_bootstrap.ipynb](notebooks/00_brownfield_baseline_and_azure_bootstrap.ipynb),
which walks through the whole release.

## Provisioning Azure

Nothing is created until the pre-provision check passes. It prints the topology,
the region, and every billable resource before anything happens.

```console
azd auth login
azd env new dev
uv run python scripts/preprovision_check.py
azd provision
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
| `scripts/` | Validation, provisioning hooks, and database bootstrap |
| `src/enterprise_agents_on_foundry/` | Importable helpers used by notebooks and scripts |
| `tests/` | Configuration, safety, structure, and infrastructure contracts |

## Quality gate

```console
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src scripts
az bicep build --file infra/main.bicep
```

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
