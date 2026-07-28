---
title: AdventureWorksLT Seeding Fallback
description: Fallback procedure for populating AdventureWorksLT when built-in sample provisioning is unavailable, plus the read-only grant applied to the agent identity.
author: shiva
ms.date: 2026-07-27
ms.topic: how-to
keywords:
  - adventureworks-lt
  - azure sql
  - seeding
estimated_reading_time: 4
---

## Why this directory exists

Azure SQL can populate `AdventureWorksLT` from the control plane. The
`Microsoft.Sql/servers/databases` resource exposes a `sampleName` property whose
enumeration includes `AdventureWorksLT`, verified on 2026-07-27 against API
version `2023-08-01`. When `sqlUseBuiltInSample` is `true`, the database arrives
already populated and nothing in this directory needs to run.

The fallback below covers two situations: the property stops being honoured for
the chosen SKU, or a database was created before the flag was set.

## Separation of concerns

Three activities are kept apart on purpose, because conflating them is what made
the legacy project hard to reason about.

Provisioning the server and database is a control-plane concern. It belongs in
Bicep, is idempotent by nature, and is reviewed as infrastructure.

Populating data is a data-plane concern. It needs a database connection, an
authenticated principal, and a safety switch, so it belongs in a script.

Validating connectivity and running smoke queries is a third concern again. It
never modifies anything and can run against any environment at any time.

## Fallback procedure

Use this only when `sqlUseBuiltInSample` was `false` or the sample did not
materialise.

1. Confirm the target. Run the bootstrap script in inspection mode first, which
   prints the server and database it resolved and exits without connecting:

   ```console
   uv run --extra database python scripts/bootstrap_database.py
   ```

   With `ALLOW_DATABASE_BOOTSTRAP` unset or `false`, the script refuses to
   proceed. That refusal is the expected result on a first run.

2. Download the official sample. Microsoft publishes `AdventureWorksLT` as a
   `.bacpac` on the [Azure SQL samples repository](https://github.com/Microsoft/sql-server-samples/releases).
   Do not commit the file. The repository `.gitignore` excludes `*.bacpac`.

3. Import it into the existing database using `SqlPackage`, authenticating with
   Microsoft Entra rather than a SQL login:

   ```console
   SqlPackage /Action:Import ^
     /SourceFile:"AdventureWorksLT.bacpac" ^
     /TargetServerName:"<server>.database.windows.net" ^
     /TargetDatabaseName:"AdventureWorksLT" ^
     /TargetAuthenticationType:"ActiveDirectoryInteractive"
   ```

4. Re-run validation to confirm the schema and row counts match expectations:

   ```console
   uv run --extra database python scripts/bootstrap_database.py --validate-only
   ```

## Read-only grant for the agent identity

`grant_readonly_agent_access.sql` creates a contained database user for the
user-assigned managed identity and adds it to `db_datareader`. It then denies
`INSERT`, `UPDATE`, `DELETE`, `ALTER`, and `EXECUTE` explicitly.

Two points matter here.

Database permissions cannot be expressed through Azure role-based access
control. A control-plane role such as SQL DB Contributor governs the resource,
not the rows, so the grant has to happen inside the database.

The deny statements are redundant against `db_datareader` alone. They are kept
so that adding a broader role later does not silently widen what the agent can
do, since `DENY` outranks `GRANT` in SQL Server.

## Out of scope

Full AdventureWorks OLTP is not part of v0.1. The lightweight variant has enough
surface for joins, aggregates, grouping, and date filtering while staying small
enough to reason about during the lessons.
