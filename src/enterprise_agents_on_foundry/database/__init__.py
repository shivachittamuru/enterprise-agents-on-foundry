"""The database boundary.

Every read against Azure SQL passes through this package. Callers work with
typed requests and results and never see a cursor, a connection string, or a
token. Natural-language-to-SQL and agent tooling are deliberately absent: this
release establishes the boundary those later releases will sit behind.
"""

from enterprise_agents_on_foundry.database.connection import (
    REQUIRED_ODBC_DRIVER,
    DatabaseClient,
    assert_odbc_driver_available,
    connect,
    installed_odbc_drivers,
)
from enterprise_agents_on_foundry.database.metadata import (
    list_schemas,
    list_tables,
    load_query,
    run_connectivity_probe,
    run_smoke_query,
    total_approximate_rows,
)
from enterprise_agents_on_foundry.database.models import (
    DatabaseHealth,
    DatabaseTarget,
    QueryRequest,
    QueryResult,
    SchemaInfo,
    TableInfo,
)
from enterprise_agents_on_foundry.database.validation import (
    assert_read_only_sql,
    is_read_only_sql,
    require_bootstrap_allowed,
    resolve_database_target,
    split_statements,
    strip_sql_comments,
)

__all__ = [
    "REQUIRED_ODBC_DRIVER",
    "DatabaseClient",
    "DatabaseHealth",
    "DatabaseTarget",
    "QueryRequest",
    "QueryResult",
    "SchemaInfo",
    "TableInfo",
    "assert_odbc_driver_available",
    "assert_read_only_sql",
    "connect",
    "installed_odbc_drivers",
    "is_read_only_sql",
    "list_schemas",
    "list_tables",
    "load_query",
    "require_bootstrap_allowed",
    "resolve_database_target",
    "run_connectivity_probe",
    "run_smoke_query",
    "split_statements",
    "strip_sql_comments",
    "total_approximate_rows",
]
