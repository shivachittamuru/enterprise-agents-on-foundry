---
title: Modernization Risks
description: Ranked risks carried by the brownfield backend, the evidence for each, and how the v0.1 foundation addresses or defers them.
author: shiva
ms.date: 2026-07-27
ms.topic: concept
keywords:
  - risk
  - security
  - modernization
estimated_reading_time: 9
---

## How to read this

Each risk names the evidence in the legacy repository, states the consequence,
and records the v0.1 position. Some risks are closed by this release. Others are
deliberately deferred, and saying so explicitly is more useful than implying
coverage that does not exist.

## R1. Generated SQL is unconstrained

Evidence: `agents/sql_agent/prompts.py` asks the model not to issue DML.
`agents/sql_agent/tools.py` connects with full rights to the database file.
Nothing inspects the generated statement.

Consequence: a prompt injection carried in database content, or a single
unlucky generation, can modify or destroy data. The only barrier is model
compliance.

Position in v0.1: closed with two independent controls. `assert_read_only_sql`
in [database.py](../../src/enterprise_agents_on_foundry/setup/database.py)
rejects anything that is not a single `SELECT` or `WITH` statement, strips
comments before matching, tracks string literals so a semicolon inside a value
is not treated as a statement separator, and blocks 28 keywords plus the `sp_`
and `xp_` prefixes. Independently, the agent principal is granted
`db_datareader` and denied write permissions inside the database. Neither
control depends on the other, and neither depends on the prompt.

## R2. Credentials are keys and passwords in environment variables

Evidence: `.env.example` declares `AZURE_OPENAI_API_KEY` and
`PG_VECTOR_PASSWORD`. `deployment-guide.md` passes secrets as `-e` flags on the
`docker run` command line.

Consequence: secrets appear in shell history, process listings, and container
inspection output. Rotation requires a redeploy. There is no audit trail
distinguishing one caller from another.

Position in v0.1: closed. The Foundry resource sets `disableLocalAuth: true`, so
key-based access is not merely discouraged but unavailable. Azure SQL sets
`azureADOnlyAuthentication: true` and the Bicep module declares no
`administratorLogin` or `administratorLoginPassword` parameter at all. The
settings model has no field whose name contains password, secret, or key, and a
test asserts that. Access is granted through role assignments to a user-assigned
managed identity.

## R3. State and sample data are committed binaries inside the image

Evidence: three `state.db` files totalling roughly 5 MB at
`agents/checkpointer_db/`, `api/checkpointer_db/`, and
`notebooks/checkpointer_db/`, plus two copies of `chinook.db`.

Consequence: real conversation content is in version control. State is lost on
restart and diverges per replica. Every image ships several megabytes of stale
data. Diffs on these files are unreviewable.

Position in v0.1: closed for the data layer, deferred for checkpointing.
AdventureWorksLT lives in Azure SQL and is populated by the platform. `.gitignore`
excludes `*.db`, `*.sqlite`, `*.sqlite3`, `*.bacpac`, and `checkpointer_db/`, and
a test walks the tree to confirm none are present. Durable conversation
checkpointing is a later release, because there is no agent to checkpoint yet.

## R4. Dependencies are pinned but not locked

Evidence: `requirements.txt` with 31 exact pins, no lockfile, no hashes, no
groups, one undeclared import, and two beta packages.

Consequence: builds are not reproducible, and the missing `pydantic-settings`
declaration means a working install depends on a transitive accident.

Position in v0.1: closed. See
[dependency-inventory.md](dependency-inventory.md).

## R5. Modules perform work at import time

Evidence: `agents/sql_agent/tools.py` constructs `AzureChatOpenAI` and
`SQLDatabase.from_uri("sqlite:///chinook.db")` at module scope.

Consequence: the module cannot be imported without credentials, and cannot be
imported from a directory that lacks the database file. This blocks unit
testing and makes behaviour depend on the working directory.

Position in v0.1: closed by convention and by structure. No module in
`src/enterprise_agents_on_foundry/` performs external work at import time, paths
resolve through `repository_root()` anchored on `__file__` rather than the
working directory, and the test suite runs with no Azure connectivity.

## R6. Configuration is evaluated once, at class definition

Evidence: `api/settings.py` supplies field defaults with `os.environ.get(...)`
in the class body.

Consequence: variables set after import are invisible, and an unset variable
produces `None` in a field declared as `str`, so the type annotation is false.

Position in v0.1: closed. `Settings` uses pydantic-settings with declared
defaults and validators. Empty strings become `None` rather than blank values.
The model is frozen. Process environment variables take precedence over `.env`
file values, which is the reverse of the legacy `load_dotenv(override=True)`
behaviour and the safer default for CI.

## R7. Observability is configured but inactive

Evidence: `agents/tracing.py` reads `APPINSIGHTS_CONNECTIONSTRING`, while
`.env.example` defines `APPLICATIONINSIGHTS_CONNECTION_STRING`.

Consequence: the system appears instrumented and exports nothing. The failure is
silent, which is worse than having no tracing.

Position in v0.1: partially closed. A workspace-based Application Insights
resource and its Log Analytics workspace are provisioned, the canonical variable
name is used in `.env.example`, and `DisableLocalAuth: true` requires Entra
authentication for ingestion. Instrumenting an application is a later release,
because there is no application yet.

## R8. The service leaks internal detail

Evidence: `api/service.py` prints full request keyword arguments including user
input, and returns `HTTPException(detail=str(e))`.

Consequence: user content reaches container logs with no redaction, and internal
exception text reaches callers.

Position in v0.1: not applicable yet, and carried forward. There is no service in
this release. The constraint is recorded here so the replacement service is
written against it rather than repeating the pattern.

## R9. CORS is open with credentials enabled

Evidence: `CORSMiddleware` with `allow_origins=["*"]` and
`allow_credentials=True`.

Consequence: the combination is rejected by browsers, so it is simultaneously
insecure in intent and non-functional in effect.

Position in v0.1: not applicable yet, carried forward with the service.

## R10. Deployment is manual and undocumented in code

Evidence: `deployment-guide.md` describes a hand-run sequence of `docker build`,
`az acr login`, `docker push`, and portal steps for Web App or AKS. Evaluation
code contains the hardcoded address `http://52.248.105.188:80`.

Consequence: environments drift, nothing is reviewable, and teardown is manual,
so cost accumulates silently.

Position in v0.1: closed for infrastructure. The topology is modular Bicep under
the azd lifecycle, compiled in CI terms by `az bicep build`, and removable with
`azd down`. No endpoint address appears in code.

## R11. Two implementations of the same agent

Evidence: `sql_agent.py` at 12.7 KB and `sql_agent_postgres.py` at 13.5 KB.

Consequence: a fix applied to one is not applied to the other, and there is no
way to tell which is authoritative.

Position in v0.1: not applicable yet. The agent is rewritten in a later release
with a single implementation and a configurable persistence backend.

## R12. Unreachable code presented as a feature

Evidence: `agents/visualization_agent/` is complete, imports `plotly`, and is
never referenced by the service.

Consequence: the dependency is shipped, the code is unmaintained, and readers
assume a capability that does not exist.

Position in v0.1: closed by omission. The visualization agent is not carried
forward. If charting returns, it returns as a wired, tested component.

## Risks introduced by the modernization

Being honest about new risk matters as much as closing old risk.

Serverless Azure SQL auto-pauses after sixty minutes of inactivity, so the first
query after an idle period pays a resume delay of roughly one minute. This is a
deliberate trade of latency for cost in a teaching environment, and it is not
appropriate for production.

The Bicep templates open the Azure services firewall rule
`AllowAllWindowsAzureIps` so that Foundry can reach the database. This is
broader than a private endpoint. Private networking is available behind
`enablePrivateNetworking` and is off by default in v0.1 to keep the first
provisioning simple.

The requested model was not available, and the substitute is recorded in
[004-model-deployment-selection.md](../adr/004-model-deployment-selection.md)
rather than applied silently.

## Related

- [backend-inventory.md](backend-inventory.md)
- [current-architecture.md](current-architecture.md)
- [../architecture/v0.1-azure-foundation.md](../architecture/v0.1-azure-foundation.md)
