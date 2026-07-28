"""Integration tests against the provisioned Azure SQL database.

These are marked ``azure`` and are excluded from the default loop:

    uv run pytest -m "not azure"    # no cloud access needed
    uv run pytest -m azure          # requires a provisioned environment

They skip rather than fail when the environment is not provisioned, when the
ODBC driver is missing, or when no Entra credential is available, so a fresh
clone can still run the full command.

Everything here is read-only. Nothing in this file modifies the database.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from enterprise_agents_on_foundry.config.settings import Settings
from enterprise_agents_on_foundry.database.connection import DatabaseClient, connect
from enterprise_agents_on_foundry.database.metadata import (
    list_schemas,
    list_tables,
    run_connectivity_probe,
    run_smoke_query,
    total_approximate_rows,
)
from enterprise_agents_on_foundry.database.models import QueryRequest
from enterprise_agents_on_foundry.database.validation import resolve_database_target
from enterprise_agents_on_foundry.errors import DatabaseConnectionError, QueryValidationError

pytestmark = pytest.mark.azure


@pytest.fixture(scope="module")
def client(azure_settings: Settings) -> Iterator[DatabaseClient]:
    """Open one connection for the module.

    Serverless Azure SQL can be paused, and the first connection pays the resume
    cost, so the connection is shared rather than reopened per test.
    """
    try:
        connection = connect(azure_settings)
    except DatabaseConnectionError as error:
        pytest.skip(f"Cannot reach the database: {error}")

    with connection as opened:
        yield opened


def test_target_resolves_to_an_azure_sql_endpoint(azure_settings: Settings) -> None:
    target = resolve_database_target(azure_settings)

    assert target.server_fqdn.endswith(".database.windows.net")
    assert target.authentication == "entra"


def test_connection_reports_healthy(client: DatabaseClient) -> None:
    health = client.health_check()

    assert health.connected is True
    assert health.elapsed_ms >= 0


def test_connectivity_probe_returns_a_row(client: DatabaseClient) -> None:
    result = run_connectivity_probe(client)

    assert result.row_count >= 1


def test_schema_discovery_finds_saleslt(client: DatabaseClient) -> None:
    schemas = {schema.name for schema in list_schemas(client)}

    assert "SalesLT" in schemas, f"expected the AdventureWorksLT sample schema, found: {sorted(schemas)}"


def test_table_discovery_finds_the_expected_sample_tables(client: DatabaseClient) -> None:
    names = {table.qualified_name for table in list_tables(client)}

    assert {"SalesLT.Product", "SalesLT.Customer", "SalesLT.SalesOrderHeader"} <= names


def test_the_database_is_not_empty(client: DatabaseClient) -> None:
    """A provisioned but unseeded database is the failure this catches."""
    assert total_approximate_rows(list_tables(client)) > 0


def test_smoke_query_returns_product_categories(client: DatabaseClient) -> None:
    result = run_smoke_query(client)

    assert result.row_count > 0
    assert result.columns


def test_row_limit_is_enforced_by_the_client(client: DatabaseClient) -> None:
    """The limit has to hold against the server, not only in unit tests."""
    result = client.execute(QueryRequest(sql="SELECT * FROM SalesLT.Product", max_rows=5))

    assert result.row_count == 5
    assert result.truncated is True


def test_a_write_is_refused_before_it_reaches_the_server(client: DatabaseClient) -> None:
    """The validator, not database permissions, is the first line of defence."""
    with pytest.raises(QueryValidationError):
        client.execute_sql("DELETE FROM SalesLT.Product")


def test_results_never_expose_the_access_token(client: DatabaseClient) -> None:
    """Notebook output is shared, so a result must be safe to paste."""
    rendered = run_smoke_query(client).format_table()

    assert "eyJ" not in rendered, "an access token appears to have leaked into a result"
