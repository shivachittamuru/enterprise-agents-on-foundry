"""``eaof-db-info``: inspect the configured AdventureWorksLT database, read-only.

Connects, checks health, lists schemas and tables, and runs one representative
query. It modifies nothing and needs no safety switch, because every statement it
issues comes from ``database/queries/`` and passes the read-only validator first.

Exit codes:

* ``0`` connected and every read succeeded
* ``1`` connected but the database did not answer as expected
* ``2`` configuration, driver, or credential is unavailable
"""

from __future__ import annotations

import sys

from enterprise_agents_on_foundry.cli.console import EXIT_FAILED, EXIT_OK, EXIT_UNAVAILABLE, section
from enterprise_agents_on_foundry.config.settings import load_settings
from enterprise_agents_on_foundry.database.connection import connect
from enterprise_agents_on_foundry.database.metadata import (
    list_schemas,
    list_tables,
    run_smoke_query,
    total_approximate_rows,
)
from enterprise_agents_on_foundry.database.validation import resolve_database_target
from enterprise_agents_on_foundry.errors import (
    ConfigurationError,
    DatabaseConnectionError,
    QueryValidationError,
    UnsafeDatabaseTargetError,
)


def main(argv: list[str] | None = None) -> int:
    """Inspect the configured database and return a process exit code."""
    if argv:
        print(f"eaof-db-info takes no arguments; received: {' '.join(argv)}")
        return EXIT_UNAVAILABLE

    try:
        settings = load_settings()
        target = resolve_database_target(settings)
    except (ConfigurationError, UnsafeDatabaseTargetError) as error:
        print(error)
        return EXIT_UNAVAILABLE

    section("Target")
    print(f"  {target.display}")
    print(f"  Dataset: {settings.dataset_variant}")
    print("  No password is used or stored. Access is Microsoft Entra only.")

    try:
        client = connect(settings)
    except DatabaseConnectionError as error:
        section("Result: UNAVAILABLE")
        print(f"  {error}")
        return EXIT_UNAVAILABLE

    with client:
        health = client.health_check()
        section("Health")
        print(f"  {health.summary}")
        if not health.connected:
            return EXIT_FAILED
        if health.server_version:
            print(f"  {health.server_version}")

        try:
            schemas = list_schemas(client)
            tables = list_tables(client)
            smoke = run_smoke_query(client)
        except (DatabaseConnectionError, QueryValidationError) as error:
            section("Result: FAILED")
            print(f"  {error}")
            return EXIT_FAILED

        section(f"Schemas ({len(schemas)})")
        for schema in schemas:
            print(f"  {schema.name:<24} {schema.table_count} table(s)")

        section(f"Tables ({len(tables)}, {total_approximate_rows(tables)} approximate rows)")
        for table in tables:
            print(f"  {table.qualified_name:<40} {table.approximate_rows:>8}")

        section(f"Smoke query ({smoke.elapsed_ms} ms)")
        print(smoke.format_table())

    section("Result: OK")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
