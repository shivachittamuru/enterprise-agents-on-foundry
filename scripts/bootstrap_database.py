"""Azure SQL bootstrap and validation for AdventureWorksLT.

Safety model:

* The target server and database are resolved and validated before anything
  else happens, and printed so you can see what is about to be touched.
* Nothing is modified unless ``ALLOW_DATABASE_BOOTSTRAP=true``. Without it the
  script runs read-only checks and exits.
* Every statement it runs against the database is either a read-only query from
  ``database/queries/`` or the idempotent grant script from
  ``database/seeds/adventureworks-lt/``.
* No statement drops, truncates, or deletes anything.
* Authentication uses the Microsoft Entra identity chain. No password is read,
  stored, or printed.

Usage:
    uv run --extra database python scripts/bootstrap_database.py
    uv run --extra database python scripts/bootstrap_database.py --validate-only
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from enterprise_agents_on_foundry.setup.config import Settings, load_settings, repository_root
from enterprise_agents_on_foundry.setup.database import (
    DatabaseTarget,
    DatabaseTargetError,
    assert_odbc_driver_available,
    assert_read_only_sql,
    require_bootstrap_allowed,
    resolve_database_target,
)
from enterprise_agents_on_foundry.setup.measurements import MeasurementSet

_ODBC_HINT = (
    "pyodbc and the Microsoft ODBC Driver 18 for SQL Server are required for "
    "database validation. Install the extra with 'uv sync --extra database' and "
    "the driver from https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server"
)


def _print_header(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


def _connect(target: DatabaseTarget, settings: Settings) -> Any:
    """Open an Entra-authenticated connection, or exit with a readable message."""
    try:
        import pyodbc
    except ImportError:
        print(_ODBC_HINT)
        raise SystemExit(2) from None

    # Checked before connecting so a missing driver reports itself rather than
    # surfacing as pyodbc IM002.
    try:
        assert_odbc_driver_available()
    except DatabaseTargetError as error:
        print(f"\n{error}")
        raise SystemExit(2) from None

    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
    token = credential.get_token("https://database.windows.net/.default")

    # SQL_COPT_SS_ACCESS_TOKEN. The token is passed to the driver rather than
    # embedded in the connection string so it never appears in a log.
    token_bytes = token.token.encode("utf-16-le")
    token_struct = len(token_bytes).to_bytes(4, "little") + token_bytes

    return pyodbc.connect(
        target.odbc_connection_string(connect_timeout=settings.database_connect_timeout_seconds),
        attrs_before={1256: token_struct},
        timeout=settings.database_connect_timeout_seconds,
    )


def _run_query_file(connection: Any, path: Path, settings: Settings) -> list[tuple[Any, ...]]:
    """Run a validated read-only query file and return capped rows."""
    sql = assert_read_only_sql(path.read_text(encoding="utf-8"))
    cursor = connection.cursor()
    try:
        cursor.execute(sql)
        return [tuple(row) for row in cursor.fetchmany(settings.database_max_result_rows)]
    finally:
        cursor.close()


def _show_target(target: DatabaseTarget, settings: Settings) -> None:
    _print_header("Target")
    print(f"  Server        : {target.server_fqdn}")
    print(f"  Database      : {target.database_name}")
    print(f"  Authentication: {target.authentication} (Microsoft Entra, no password)")
    print(f"  Dataset       : {settings.dataset_variant}")
    print(f"  Bootstrap     : {'ENABLED' if settings.allow_database_bootstrap else 'disabled'}")


def _validate(connection: Any, settings: Settings, measurements: MeasurementSet) -> None:
    """Run the read-only inventory and smoke queries."""
    queries_dir = repository_root() / "database" / "queries"

    _print_header("Connectivity")
    started = time.perf_counter()
    _run_query_file(connection, queries_dir / "00_connectivity_probe.sql", settings)
    probe_ms = round((time.perf_counter() - started) * 1000, 1)
    measurements.add("sql_probe_latency", probe_ms, unit="ms", category="database")
    print(f"  Probe query returned in {probe_ms} ms.")

    _print_header("Schemas")
    schemas = _run_query_file(connection, queries_dir / "01_schema_inventory.sql", settings)
    for schema_name, table_count in schemas:
        print(f"  {schema_name:<24} {table_count} table(s)")
    measurements.add("schema_count", len(schemas), category="database")

    _print_header("Tables and approximate row counts")
    tables = _run_query_file(connection, queries_dir / "02_table_row_counts.sql", settings)
    for schema_name, table_name, rows in tables:
        print(f"  {schema_name}.{table_name:<28} {rows:>8}")
    measurements.add("table_count", len(tables), category="database")
    measurements.add("total_row_count", sum(int(row[2] or 0) for row in tables), unit="rows", category="database")

    _print_header("Smoke query")
    started = time.perf_counter()
    categories = _run_query_file(connection, queries_dir / "03_smoke_product_categories.sql", settings)
    query_ms = round((time.perf_counter() - started) * 1000, 1)
    measurements.add("sql_simple_query_latency", query_ms, unit="ms", category="database")
    for category_name, product_count, average_price in categories:
        print(f"  {category_name:<24} {product_count:>4} products, avg list price {average_price}")
    print(f"  Completed in {query_ms} ms.")


def _apply_grant(connection: Any, identity_name: str) -> None:
    """Apply the idempotent read-only grant for the agent identity."""
    script_path = repository_root() / "database" / "seeds" / "adventureworks-lt" / "grant_readonly_agent_access.sql"
    sql = script_path.read_text(encoding="utf-8").replace("{{AGENT_IDENTITY_NAME}}", identity_name)

    _print_header("Applying read-only grant")
    print(f"  Identity: {identity_name}")
    print("  Roles   : db_datareader only, with INSERT/UPDATE/DELETE/ALTER/EXECUTE denied.")

    cursor = connection.cursor()
    try:
        cursor.execute(sql)
        connection.commit()
    finally:
        cursor.close()
    print("  Grant applied. Rerunning this script makes no further change.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and bootstrap the AdventureWorksLT database.")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Run read-only checks even if bootstrap is enabled.",
    )
    parser.add_argument(
        "--agent-identity-name",
        default=None,
        help="Display name of the managed identity to grant read-only access.",
    )
    parser.add_argument(
        "--measurements-out",
        type=Path,
        default=None,
        help="Optional path for a JSON measurement file.",
    )
    args = parser.parse_args(argv)

    settings = load_settings()

    try:
        target = resolve_database_target(settings)
    except DatabaseTargetError as error:
        print(f"Database target is not valid: {error}")
        return 1

    _show_target(target, settings)

    will_modify = settings.allow_database_bootstrap and not args.validate_only
    if not will_modify:
        _print_header("Mode")
        if args.validate_only:
            print("  Validation only. Nothing will be modified.")
        else:
            try:
                require_bootstrap_allowed(settings)
            except PermissionError as error:
                print(f"  {error}")
            print("  Continuing with read-only validation.")

    measurements = MeasurementSet()
    connection = _connect(target, settings)
    try:
        _validate(connection, settings, measurements)
        if will_modify:
            identity_name = args.agent_identity_name
            if not identity_name:
                print("\n  --agent-identity-name was not supplied, so no grant was applied.")
            else:
                _apply_grant(connection, identity_name)
    finally:
        connection.close()

    if args.measurements_out:
        written = measurements.write_json(args.measurements_out)
        print(f"\nMeasurements written to {written}")

    _print_header("Result: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
