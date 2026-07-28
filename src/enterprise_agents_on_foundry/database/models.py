"""Typed request and response models for the database boundary.

Inputs are pydantic models, because a query request can arrive from a notebook,
a CLI argument, or later from an agent, and must be validated at the boundary.
Outputs are frozen dataclasses, because this package constructs them itself and
re-validating our own data would buy nothing.

No model carries a credential, a token, or a connection string with a secret in
it, so every model here is safe to print.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

REQUIRED_ODBC_DRIVER: Final = "ODBC Driver 18 for SQL Server"
"""Driver 17 predates several Entra authentication modes and is not accepted."""

__all__ = [
    "REQUIRED_ODBC_DRIVER",
    "DatabaseHealth",
    "DatabaseTarget",
    "QueryRequest",
    "QueryResult",
    "SchemaInfo",
    "TableInfo",
]


@dataclass(frozen=True, slots=True)
class DatabaseTarget:
    """A validated Azure SQL target."""

    server_name: str
    server_fqdn: str
    database_name: str
    authentication: str

    @property
    def display(self) -> str:
        """A short, log-safe description of the target."""
        return f"{self.server_fqdn}/{self.database_name} (auth={self.authentication})"

    def odbc_connection_string(self, *, connect_timeout: int = 30) -> str:
        """Build an Entra ODBC connection string.

        No password is ever embedded. Authentication is delegated to the Azure
        identity chain and the token is attached to the driver separately, so
        this string is safe to display.
        """
        return (
            f"Driver={{{REQUIRED_ODBC_DRIVER}}};"
            f"Server=tcp:{self.server_fqdn},1433;"
            f"Database={self.database_name};"
            "Encrypt=yes;"
            "TrustServerCertificate=no;"
            f"Connection Timeout={connect_timeout};"
        )


class QueryRequest(BaseModel):
    """One read-only query and the limits it must run under."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sql: str = Field(min_length=1)
    max_rows: int = Field(default=500, ge=1, le=10_000)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    label: str | None = Field(default=None)

    @field_validator("sql")
    @classmethod
    def _strip_sql(cls, value: str) -> str:
        """Trim surrounding whitespace so a file-loaded query compares cleanly."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("sql must not be blank")
        return stripped


@dataclass(frozen=True, slots=True)
class QueryResult:
    """The outcome of one read-only query."""

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool
    elapsed_ms: float
    label: str | None = None

    @property
    def row_count(self) -> int:
        """Rows returned, after truncation."""
        return len(self.rows)

    def as_dicts(self) -> list[dict[str, Any]]:
        """Return the rows keyed by column name."""
        return [dict(zip(self.columns, row, strict=False)) for row in self.rows]

    def format_table(self, *, limit: int = 20) -> str:
        """Render the first ``limit`` rows as fixed-width text."""
        if not self.columns:
            return "(no columns)"

        shown = self.rows[:limit]
        widths = [
            max(len(column), *(len(str(row[index])) for row in shown)) if shown else len(column)
            for index, column in enumerate(self.columns)
        ]
        header = "  ".join(column.ljust(widths[index]) for index, column in enumerate(self.columns))
        divider = "  ".join("-" * width for width in widths)
        body = ["  ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)) for row in shown]
        lines = [header, divider, *body]
        if len(self.rows) > limit:
            lines.append(f"... {len(self.rows) - limit} more row(s)")
        if self.truncated:
            lines.append("(result truncated at the configured row limit)")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class SchemaInfo:
    """One schema and how many tables it contains."""

    name: str
    table_count: int


@dataclass(frozen=True, slots=True)
class TableInfo:
    """One table and its approximate row count."""

    schema_name: str
    table_name: str
    approximate_rows: int

    @property
    def qualified_name(self) -> str:
        """The two-part name used in queries."""
        return f"{self.schema_name}.{self.table_name}"


@dataclass(frozen=True, slots=True)
class DatabaseHealth:
    """Whether the configured target is reachable."""

    target: DatabaseTarget
    connected: bool
    elapsed_ms: float
    server_version: str | None = None
    error: str | None = None

    @property
    def summary(self) -> str:
        """One line describing the outcome."""
        if self.connected:
            return f"Connected to {self.target.display} in {self.elapsed_ms} ms."
        return f"Could not connect to {self.target.display}: {self.error}"
