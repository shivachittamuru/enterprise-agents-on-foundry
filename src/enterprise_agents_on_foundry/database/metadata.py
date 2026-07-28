"""Schema and table discovery against AdventureWorksLT.

The SQL lives in ``database/queries/`` rather than in Python string literals, so
there is exactly one copy of each statement, the repository structure test can
validate every file against the read-only validator, and a reader can run the
same query by hand in any client.

Nothing here interprets natural language or generates SQL. Discovery is fixed,
deterministic, and read-only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from enterprise_agents_on_foundry.config.settings import repository_root
from enterprise_agents_on_foundry.database.connection import DatabaseClient
from enterprise_agents_on_foundry.database.models import QueryResult, SchemaInfo, TableInfo
from enterprise_agents_on_foundry.database.validation import assert_read_only_sql
from enterprise_agents_on_foundry.errors import UnsafeDatabaseTargetError

CONNECTIVITY_PROBE: Final = "00_connectivity_probe"
SCHEMA_INVENTORY: Final = "01_schema_inventory"
TABLE_ROW_COUNTS: Final = "02_table_row_counts"
SMOKE_PRODUCT_CATEGORIES: Final = "03_smoke_product_categories"

__all__ = [
    "CONNECTIVITY_PROBE",
    "SCHEMA_INVENTORY",
    "SMOKE_PRODUCT_CATEGORIES",
    "TABLE_ROW_COUNTS",
    "list_schemas",
    "list_tables",
    "load_query",
    "queries_directory",
    "query_path",
    "run_connectivity_probe",
    "run_smoke_query",
    "total_approximate_rows",
]


def queries_directory() -> Path:
    """Return the directory holding the read-only query files."""
    return repository_root() / "database" / "queries"


def query_path(name: str) -> Path:
    """Resolve a query file by stem, refusing anything outside the query directory."""
    directory = queries_directory()
    candidate = (directory / f"{name}.sql").resolve()
    if candidate.parent != directory.resolve():
        raise UnsafeDatabaseTargetError(f"'{name}' does not name a query in {directory}.")
    if not candidate.is_file():
        raise UnsafeDatabaseTargetError(f"Query file '{candidate.name}' was not found in {directory}.")
    return candidate


def load_query(name: str) -> str:
    """Read and validate a query file, returning the single statement it contains."""
    return assert_read_only_sql(query_path(name).read_text(encoding="utf-8"))


def run_connectivity_probe(client: DatabaseClient) -> QueryResult:
    """Run the smallest query that proves an authenticated session works."""
    return client.execute_sql(load_query(CONNECTIVITY_PROBE), label=CONNECTIVITY_PROBE)


def list_schemas(client: DatabaseClient) -> tuple[SchemaInfo, ...]:
    """Return the user schemas in the database with their table counts."""
    result = client.execute_sql(load_query(SCHEMA_INVENTORY), label=SCHEMA_INVENTORY)
    return tuple(SchemaInfo(name=str(row[0]), table_count=int(row[1] or 0)) for row in result.rows)


def list_tables(client: DatabaseClient) -> tuple[TableInfo, ...]:
    """Return the tables with approximate row counts taken from partition metadata."""
    result = client.execute_sql(load_query(TABLE_ROW_COUNTS), label=TABLE_ROW_COUNTS)
    return tuple(
        TableInfo(schema_name=str(row[0]), table_name=str(row[1]), approximate_rows=int(row[2] or 0))
        for row in result.rows
    )


def total_approximate_rows(tables: tuple[TableInfo, ...]) -> int:
    """Sum the approximate row counts across ``tables``."""
    return sum(table.approximate_rows for table in tables)


def run_smoke_query(client: DatabaseClient) -> QueryResult:
    """Run the representative join, aggregate, group, and order query."""
    return client.execute_sql(load_query(SMOKE_PRODUCT_CATEGORIES), label=SMOKE_PRODUCT_CATEGORIES)
