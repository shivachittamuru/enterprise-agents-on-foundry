"""Azure SQL validation and the one write path in the repository.

Safety model:

* The target is resolved and printed before anything else happens.
* Nothing is modified unless ``ALLOW_DATABASE_BOOTSTRAP=true`` and an identity
  name is supplied. Without both, the script runs read-only checks and exits.
* Read-only work is delegated to ``enterprise_agents_on_foundry.database``, which
  validates every statement before it reaches the server.
* The only statement that changes anything is the idempotent grant script in
  ``database/seeds/adventureworks-lt/``. It drops, truncates, and deletes nothing.
* Authentication uses the Microsoft Entra identity chain. No password is read,
  stored, or printed.

Usage:
    uv run --extra database python scripts/bootstrap_database.py
    uv run --extra database python scripts/bootstrap_database.py --validate-only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from enterprise_agents_on_foundry.cli.console import EXIT_FAILED, EXIT_OK, EXIT_UNAVAILABLE, section
from enterprise_agents_on_foundry.config.settings import Settings, load_settings, repository_root
from enterprise_agents_on_foundry.database.connection import DatabaseClient, connect
from enterprise_agents_on_foundry.database.metadata import (
    list_schemas,
    list_tables,
    run_connectivity_probe,
    run_smoke_query,
    total_approximate_rows,
)
from enterprise_agents_on_foundry.database.validation import require_bootstrap_allowed, resolve_database_target
from enterprise_agents_on_foundry.errors import EaofError
from enterprise_agents_on_foundry.observability.measurements import MeasurementSet


def _validate(client: DatabaseClient, measurements: MeasurementSet) -> None:
    """Run the read-only inventory and smoke queries."""
    section("Connectivity")
    probe = run_connectivity_probe(client)
    measurements.add("sql_probe_latency", probe.elapsed_ms, unit="ms", category="database")
    print(f"  Probe query returned in {probe.elapsed_ms} ms.")

    section("Schemas")
    schemas = list_schemas(client)
    for schema in schemas:
        print(f"  {schema.name:<24} {schema.table_count} table(s)")
    measurements.add("schema_count", len(schemas), category="database")

    section("Tables and approximate row counts")
    tables = list_tables(client)
    for table in tables:
        print(f"  {table.qualified_name:<40} {table.approximate_rows:>8}")
    measurements.add("table_count", len(tables), category="database")
    measurements.add("total_row_count", total_approximate_rows(tables), unit="rows", category="database")

    section("Smoke query")
    smoke = run_smoke_query(client)
    measurements.add("sql_simple_query_latency", smoke.elapsed_ms, unit="ms", category="database")
    print(smoke.format_table())
    print(f"  Completed in {smoke.elapsed_ms} ms.")


def _apply_grant(client: DatabaseClient, identity_name: str) -> None:
    """Apply the idempotent read-only grant for the agent identity.

    This is the only statement in the repository that is not a SELECT, so it is
    executed here rather than through the read-only database client.
    """
    script_path = repository_root() / "database" / "seeds" / "adventureworks-lt" / "grant_readonly_agent_access.sql"
    sql = script_path.read_text(encoding="utf-8").replace("{{AGENT_IDENTITY_NAME}}", identity_name)

    section("Applying read-only grant")
    print(f"  Identity: {identity_name}")
    print("  Roles   : db_datareader only, with INSERT/UPDATE/DELETE/ALTER/EXECUTE denied.")

    connection = client.raw_connection
    cursor = connection.cursor()
    try:
        cursor.execute(sql)
        connection.commit()
    finally:
        cursor.close()
    print("  Grant applied. Rerunning this script makes no further change.")


def _show_target(settings: Settings) -> None:
    target = resolve_database_target(settings)
    section("Target")
    print(f"  Server        : {target.server_fqdn}")
    print(f"  Database      : {target.database_name}")
    print(f"  Authentication: {target.authentication} (Microsoft Entra, no password)")
    print(f"  Dataset       : {settings.dataset_variant}")
    print(f"  Bootstrap     : {'ENABLED' if settings.allow_database_bootstrap else 'disabled'}")


def main(argv: list[str] | None = None) -> int:
    """Validate the database, optionally applying the read-only grant."""
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

    try:
        settings = load_settings()
        _show_target(settings)
    except EaofError as error:
        print(f"\n{error}")
        return EXIT_UNAVAILABLE

    will_modify = settings.allow_database_bootstrap and not args.validate_only
    section("Mode")
    if args.validate_only:
        print("  Validation only. Nothing will be modified.")
    elif will_modify:
        print("  Bootstrap enabled. The read-only grant will be applied if an identity is supplied.")
    else:
        try:
            require_bootstrap_allowed(settings)
        except EaofError as error:
            print(f"  {error}")
        print("  Continuing with read-only validation.")

    measurements = MeasurementSet()
    try:
        client = connect(settings)
    except EaofError as error:
        print(f"\n{error}")
        return EXIT_UNAVAILABLE

    try:
        with client:
            _validate(client, measurements)
            if will_modify:
                if args.agent_identity_name:
                    _apply_grant(client, args.agent_identity_name)
                else:
                    print("\n  --agent-identity-name was not supplied, so no grant was applied.")
    except EaofError as error:
        section("Result: FAILED")
        print(f"  {error}")
        return EXIT_FAILED

    if args.measurements_out:
        print(f"\nMeasurements written to {measurements.write_json(args.measurements_out)}")

    section("Result: OK")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
