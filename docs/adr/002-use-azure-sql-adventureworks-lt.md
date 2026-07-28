---
title: "ADR 002: Use Azure SQL with AdventureWorksLT"
description: Decision to replace the embedded SQLite Chinook database with a serverless Azure SQL database seeded from the AdventureWorksLT sample, and to keep provisioning separate from seeding.
author: shiva
ms.date: 2026-07-27
ms.topic: concept
keywords:
  - adr
  - azure sql
  - adventureworks
estimated_reading_time: 6
---

## Status

Accepted, 2026-07-27.

## Context

The legacy agent queried `chinook.db`, a 892 KB SQLite file committed twice into
the repository and copied into the container image by `COPY . /app`.

That arrangement blocks several things the curriculum needs to teach. There is
no network boundary, so connection handling, timeouts, retries, and transient
faults never appear. There is no identity, so authentication and least-privilege
access cannot be demonstrated. There is no server-side permission model, so the
prompt-only DML restriction has no enforceable counterpart. Two committed copies
of the file can drift apart. And the database is a local file, so hosted agents
running in Azure cannot reach it at all.

Later releases cover Foundry hosted agents, observability, performance, token
economics, and governance. Each of those needs a data source that behaves like a
real one.

## Decision

Provision an Azure SQL logical server with a single serverless database named
`AdventureWorksLT`, using the General Purpose serverless tier `GP_S_Gen5` with a
maximum of one vCore, a minimum of 0.5, and a sixty-minute auto-pause delay.

Populate it through the control plane. The
`Microsoft.Sql/servers/databases` resource exposes a `sampleName` property whose
enumeration includes `AdventureWorksLT`, verified against API version
`2023-08-01` on 2026-07-27. Setting it produces a populated database with no
data-plane step.

Use the lightweight variant rather than full AdventureWorks OLTP.
AdventureWorksLT has enough surface for joins, aggregates, grouping, ordering,
and date filtering, and the schema is small enough to hold in mind while reading
a generated query. The full OLTP schema would add difficulty without adding a
teachable concept.

Authenticate with Microsoft Entra only. The server sets
`azureADOnlyAuthentication: true`, and the Bicep module declares no
`administratorLogin` or `administratorLoginPassword` parameter.

Grant the agent identity `db_datareader` and nothing else, inside the database,
using
[grant_readonly_agent_access.sql](../../database/seeds/adventureworks-lt/grant_readonly_agent_access.sql).

## Provisioning and seeding are different concerns

Conflating these is what made the legacy setup hard to reason about, so the
separation is explicit.

Provisioning the server and database is a control-plane operation. It is
declarative, idempotent, reviewed as infrastructure, and lives in Bicep.

Populating and permissioning data is a data-plane operation. It needs a database
connection, an authenticated principal, and a safety switch, so it lives in
[bootstrap_database.py](../../scripts/bootstrap_database.py), which refuses to
modify anything unless `ALLOW_DATABASE_BOOTSTRAP=true` and prints the resolved
target before acting.

Validating connectivity and inspecting the schema is a third concern. It never
modifies anything, so it runs by default and needs no switch.

## Why serverless

The database is idle most of the time in a learning repository. Serverless bills
per vCore-second and pauses after sixty idle minutes, which puts the resting
cost of the data layer near zero.

The trade is a resume delay of roughly one minute on the first query after a
pause. That is acceptable here and would not be in production, so the
[architecture document](../architecture/v0.1-azure-foundation.md) records it as a
known behaviour rather than a defect.

## Consequences

The agent now crosses a network boundary, so timeouts, connection latency, and
transient faults become observable and teachable. `DATABASE_CONNECT_TIMEOUT_SECONDS`,
`DATABASE_QUERY_TIMEOUT_SECONDS`, and `DATABASE_MAX_RESULT_ROWS` exist from the
start.

Read-only access becomes enforceable rather than advisory, because the principal
itself lacks write permission.

No database binary is committed. `.gitignore` excludes `*.db`, `*.sqlite`,
`*.sqlite3`, and `*.bacpac`, and a test asserts that none are present.

Running the queries requires `pyodbc` and the Microsoft ODBC Driver 18 for SQL
Server. Because the driver is a system package, `pyodbc` sits in the optional
`database` extra so that cloning the repository and running the tests needs no
native install.

The database is reachable by Azure services through the
`AllowAllWindowsAzureIps` firewall rule, which is broader than a private
endpoint. Private networking is available behind `enablePrivateNetworking` and
is off in v0.1.

## Alternatives considered

Keeping SQLite and hosting the file on Azure Files was rejected. It adds
deployment complexity without adding a network boundary, an identity model, or a
permission model, so none of the concepts the curriculum needs would become
available.

Azure Database for PostgreSQL was rejected because the legacy repository already
contained an unused PostgreSQL path, and reviving it would carry forward the
duplicate-implementation problem described in
[modernization-risks.md](../current-state/modernization-risks.md).

Full AdventureWorks OLTP was rejected on readability grounds, as described above.

## Related

- [003-use-azd-and-modular-bicep.md](003-use-azd-and-modular-bicep.md)
- [../../database/seeds/adventureworks-lt/README.md](../../database/seeds/adventureworks-lt/README.md)
