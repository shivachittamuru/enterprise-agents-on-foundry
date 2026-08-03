"""Azure SQL connections and read-only query execution.

Everything that touches the network lives here, so the rest of the project can
be tested without a database. Three rules hold for every path through this
module:

* Authentication is Microsoft Entra. No username or password is read, stored, or
  accepted, and the access token is handed to the driver as a connection
  attribute rather than embedded in a connection string.
* No token, credential, or raw connection attribute is ever logged or returned.
* Every statement passes :func:`assert_read_only_sql` before execution, and every
  result is truncated to the caller's row limit.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Self

from enterprise_agents_on_foundry.config.settings import Settings
from enterprise_agents_on_foundry.database.models import (
    REQUIRED_ODBC_DRIVER,
    DatabaseHealth,
    DatabaseTarget,
    QueryRequest,
    QueryResult,
)
from enterprise_agents_on_foundry.database.validation import assert_read_only_sql, resolve_database_target
from enterprise_agents_on_foundry.errors import DatabaseConnectionError
from enterprise_agents_on_foundry.observability.timing import timed

type SqlConnection = Any
"""pyodbc is an optional native dependency, so its types are not available here."""

# A resource identifier, not a credential. The token itself is requested at call
# time and is never stored on this module. The doubled slash is deliberate:
# credentials that translate a scope into a legacy resource strip the trailing
# "/.default", and Azure SQL only accepts tokens whose audience keeps the
# trailing slash ("https://database.windows.net/"). Without it the driver
# reports "The server is not currently configured to accept this token".
_TOKEN_SCOPE: Final = "https://database.windows.net//.default"  # noqa: S105

# SQL_COPT_SS_ACCESS_TOKEN. Passing the token through this attribute keeps it out
# of the connection string, and therefore out of anything that logs one.
_SQL_COPT_SS_ACCESS_TOKEN: Final = 1256

_ODBC_INSTALL_HINT: Final = (
    f"The '{REQUIRED_ODBC_DRIVER}' ODBC driver is not installed.\n"
    "It is a system package, so 'uv sync' cannot provide it.\n\n"
    "Windows: winget install --id Microsoft.msodbcsql18\n"
    "macOS:   brew install msodbcsql18\n"
    "Linux:   https://learn.microsoft.com/sql/connect/odbc/linux-mac/installing-the-microsoft-odbc-driver-for-sql-server\n\n"
    "Installing it requires administrator rights. Open a new terminal afterwards "
    "so the driver registration is picked up."
)

_PYODBC_INSTALL_HINT: Final = (
    "pyodbc is not installed. Install it with 'uv sync --extra database', "
    "which is separate from the ODBC driver itself."
)

__all__ = [
    "REQUIRED_ODBC_DRIVER",
    "DatabaseClient",
    "assert_odbc_driver_available",
    "connect",
    "installed_odbc_drivers",
]


def installed_odbc_drivers() -> tuple[str, ...]:
    """Return the ODBC drivers pyodbc can see, or an empty tuple if absent."""
    try:
        import pyodbc
    except ImportError:
        return ()
    return tuple(pyodbc.drivers())


def assert_odbc_driver_available() -> None:
    """Fail with installation guidance when the required driver is missing.

    pyodbc otherwise reports IM002 "Data source name not found", which does not
    say which driver is wanted or how to obtain it.

    Raises:
        DatabaseConnectionError: when the driver is not registered.
    """
    drivers = installed_odbc_drivers()
    if REQUIRED_ODBC_DRIVER in drivers:
        return

    found = ", ".join(drivers) if drivers else "none"
    raise DatabaseConnectionError(f"{_ODBC_INSTALL_HINT}\n\nDrivers currently visible: {found}")


def _access_token_struct(settings: Settings) -> bytes:
    """Acquire an Entra access token in the layout the ODBC driver expects.

    A developer signed in to several tenants can otherwise receive a token from
    whichever tenant the identity chain considers current, and Azure SQL refuses
    a token minted elsewhere, so the configured tenant is requested explicitly.
    """
    from azure.identity import DefaultAzureCredential

    tenant = settings.azure_tenant_id
    allowed = [tenant] if tenant else []
    requested = {"tenant_id": tenant} if tenant else {}

    try:
        credential = DefaultAzureCredential(
            exclude_interactive_browser_credential=False,
            additionally_allowed_tenants=allowed,
        )
        token = credential.get_token(_TOKEN_SCOPE, **requested)
    except Exception as error:
        raise DatabaseConnectionError(
            f"Could not acquire a Microsoft Entra token for Azure SQL: {type(error).__name__}. "
            "Run 'az login' and try again."
        ) from error

    token_bytes = token.token.encode("utf-16-le")
    return len(token_bytes).to_bytes(4, "little") + token_bytes


def _open_connection(target: DatabaseTarget, settings: Settings) -> SqlConnection:
    """Open one Entra-authenticated pyodbc connection."""
    try:
        import pyodbc
    except ImportError as error:
        raise DatabaseConnectionError(_PYODBC_INSTALL_HINT) from error

    assert_odbc_driver_available()

    try:
        return pyodbc.connect(
            target.odbc_connection_string(connect_timeout=settings.database_connect_timeout_seconds),
            attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: _access_token_struct(settings)},
            timeout=settings.database_connect_timeout_seconds,
        )
    except pyodbc.Error as error:
        # The driver echoes the connection string in some errors. It contains no
        # secret, but the token attribute must never appear, so only the SQLSTATE
        # and message are surfaced.
        raise DatabaseConnectionError(f"Could not connect to {target.display}: {error}") from error


class DatabaseClient:
    """A read-only session against one validated Azure SQL target."""

    def __init__(self, target: DatabaseTarget, settings: Settings, connection: SqlConnection) -> None:
        self._target = target
        self._settings = settings
        self._connection = connection
        self._closed = False

    @property
    def target(self) -> DatabaseTarget:
        """The validated target this client is bound to."""
        return self._target

    @property
    def max_rows(self) -> int:
        """The configured row limit applied to every result."""
        return self._settings.database_max_result_rows

    @property
    def raw_connection(self) -> SqlConnection:
        """The underlying pyodbc connection.

        This is the single escape hatch out of the read-only boundary, and it
        exists for exactly one caller: the administrative grant in
        ``scripts/bootstrap_database.py``, which must run a statement that is not
        a ``SELECT``. Nothing else in the project uses it, and no agent code ever
        should. Anything reached through this property bypasses
        :func:`assert_read_only_sql`.
        """
        return self._connection

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying connection. Safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        # A failed close must not mask the error that prompted it.
        with contextlib.suppress(Exception):
            self._connection.close()

    def health_check(self) -> DatabaseHealth:
        """Confirm the target answers, and report how long that took.

        Failure is returned rather than raised, because a health check that
        raises cannot be used to report health.
        """
        with timed() as stopwatch:
            try:
                result = self.execute_sql("SELECT @@VERSION AS server_version", label="health_check")
            except (DatabaseConnectionError, OSError) as error:
                return DatabaseHealth(
                    target=self._target,
                    connected=False,
                    elapsed_ms=stopwatch.milliseconds,
                    error=str(error),
                )

        version = str(result.rows[0][0]).splitlines()[0] if result.rows else None
        return DatabaseHealth(
            target=self._target,
            connected=True,
            elapsed_ms=stopwatch.milliseconds,
            server_version=version,
        )

    def execute(self, request: QueryRequest) -> QueryResult:
        """Run one validated read-only query and return a truncated result.

        Raises:
            QueryValidationError: when the statement is not a single read-only query.
            DatabaseConnectionError: when execution fails.
        """
        sql = assert_read_only_sql(request.sql)

        # pyodbc exposes the query timeout on the connection, not the cursor.
        self._connection.timeout = request.timeout_seconds
        cursor = self._connection.cursor()
        try:
            with timed() as stopwatch:
                try:
                    cursor.execute(sql)
                    # One extra row is fetched so truncation can be reported
                    # honestly instead of guessed from a full page.
                    fetched = cursor.fetchmany(request.max_rows + 1)
                except Exception as error:
                    raise DatabaseConnectionError(f"Query failed: {error}") from error

            columns = tuple(column[0] for column in cursor.description or ())
        finally:
            cursor.close()

        truncated = len(fetched) > request.max_rows
        rows = tuple(tuple(row) for row in fetched[: request.max_rows])
        return QueryResult(
            columns=columns,
            rows=rows,
            truncated=truncated,
            elapsed_ms=stopwatch.milliseconds,
            label=request.label,
        )

    def execute_sql(self, sql: str, *, max_rows: int | None = None, label: str | None = None) -> QueryResult:
        """Convenience wrapper that builds a :class:`QueryRequest` from settings."""
        request = QueryRequest(
            sql=sql,
            max_rows=max_rows if max_rows is not None else self._settings.database_max_result_rows,
            timeout_seconds=self._settings.database_query_timeout_seconds,
            label=label,
        )
        return self.execute(request)

    def execute_file(self, path: Path, *, max_rows: int | None = None) -> QueryResult:
        """Run a read-only query stored in ``database/queries/``."""
        return self.execute_sql(path.read_text(encoding="utf-8"), max_rows=max_rows, label=path.stem)


def connect(settings: Settings) -> DatabaseClient:
    """Resolve the configured target, connect to it, and return a client.

    Raises:
        UnsafeDatabaseTargetError: when configuration does not name a valid target.
        DatabaseConnectionError: when the driver, credential, or server is unavailable.
    """
    target = resolve_database_target(settings)
    return DatabaseClient(target, settings, _open_connection(target, settings))
