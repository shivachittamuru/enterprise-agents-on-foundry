"""Read-only SQL enforcement and safe target resolution.

The legacy backend relied on a prompt instruction ("DO NOT make any DML
statements") as its only defence against destructive SQL, which is not a control
at all. This module provides a deterministic check that runs before any statement
reaches Azure SQL, independent of what a model was told.

Defence in depth still applies: the durable control is the read-only database
role granted to the agent identity. This validator exists so that an unsafe
statement fails fast and legibly, and so that later releases have one place to
wire agent-generated SQL through.
"""

from __future__ import annotations

import re
from typing import Final

from enterprise_agents_on_foundry.config.settings import Settings
from enterprise_agents_on_foundry.database.models import DatabaseTarget
from enterprise_agents_on_foundry.errors import QueryValidationError, UnsafeDatabaseTargetError

_AZURE_SQL_SUFFIX: Final = ".database.windows.net"

_SERVER_NAME_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
_DATABASE_NAME_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")

_LINE_COMMENT: Final = re.compile(r"--[^\n]*")
_BLOCK_COMMENT: Final = re.compile(r"/\*.*?\*/", re.DOTALL)

_ALLOWED_LEADING_KEYWORDS: Final[tuple[str, ...]] = ("SELECT", "WITH")

FORBIDDEN_KEYWORDS: Final[tuple[str, ...]] = (
    "ALTER",
    "BACKUP",
    "BULK",
    "CREATE",
    "DBCC",
    "DELETE",
    "DENY",
    "DROP",
    "EXEC",
    "EXECUTE",
    "GRANT",
    "INSERT",
    "INTO",
    "KILL",
    "MERGE",
    "OPENDATASOURCE",
    "OPENQUERY",
    "OPENROWSET",
    "RECONFIGURE",
    "RESTORE",
    "REVERT",
    "REVOKE",
    "SHUTDOWN",
    "TRUNCATE",
    "UPDATE",
    "UPDATETEXT",
    "WAITFOR",
    "WRITETEXT",
)

_FORBIDDEN_PREFIXES: Final[tuple[str, ...]] = ("sp_", "xp_")

__all__ = [
    "FORBIDDEN_KEYWORDS",
    "assert_read_only_sql",
    "is_read_only_sql",
    "require_bootstrap_allowed",
    "resolve_database_target",
    "split_statements",
    "strip_sql_comments",
]


def strip_sql_comments(sql: str) -> str:
    """Remove line and block comments so keywords cannot hide inside them."""
    without_blocks = _BLOCK_COMMENT.sub(" ", sql)
    return _LINE_COMMENT.sub(" ", without_blocks)


def split_statements(sql: str) -> list[str]:
    """Split on semicolons that sit outside string literals.

    Naively splitting on ``;`` would misread a literal such as ``'a;b'`` as two
    statements, so single quotes are tracked, including the doubled-quote escape.
    """
    statements: list[str] = []
    current: list[str] = []
    in_string = False
    index = 0

    while index < len(sql):
        char = sql[index]
        if in_string:
            if char == "'":
                if index + 1 < len(sql) and sql[index + 1] == "'":
                    current.append("''")
                    index += 2
                    continue
                in_string = False
            current.append(char)
        elif char == "'":
            in_string = True
            current.append(char)
        elif char == ";":
            statements.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1

    statements.append("".join(current))
    return [statement.strip() for statement in statements if statement.strip()]


def assert_read_only_sql(sql: str) -> str:
    """Validate that ``sql`` is exactly one read-only statement and return it.

    Raises:
        QueryValidationError: with a specific reason on any violation.
    """
    if not sql or not sql.strip():
        raise QueryValidationError("Query is empty.")

    uncommented = strip_sql_comments(sql)
    statements = split_statements(uncommented)

    if not statements:
        raise QueryValidationError("Query contains no executable statement.")
    if len(statements) > 1:
        raise QueryValidationError(f"Query contains {len(statements)} statements; only a single statement is allowed.")

    statement = statements[0]
    normalised = statement.upper()

    leading = normalised.split(None, 1)[0] if normalised.split() else ""
    if leading not in _ALLOWED_LEADING_KEYWORDS:
        allowed = " or ".join(_ALLOWED_LEADING_KEYWORDS)
        raise QueryValidationError(f"Query must begin with {allowed}; found '{leading or statement[:16]}'.")

    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", normalised):
            raise QueryValidationError(f"Query contains the forbidden keyword '{keyword}'.")

    lowered = statement.lower()
    for prefix in _FORBIDDEN_PREFIXES:
        if re.search(rf"\b{prefix}", lowered):
            raise QueryValidationError(f"Query references a '{prefix}' procedure, which is not allowed.")

    return statement


def is_read_only_sql(sql: str) -> bool:
    """Return whether ``sql`` passes :func:`assert_read_only_sql`."""
    try:
        assert_read_only_sql(sql)
    except QueryValidationError:
        return False
    return True


def resolve_database_target(settings: Settings) -> DatabaseTarget:
    """Validate and return the configured Azure SQL target.

    Every field is checked before a connection is attempted so that a
    misconfigured environment fails with a readable message rather than an ODBC
    error, and so that a caller can display exactly what it is about to touch.

    Raises:
        UnsafeDatabaseTargetError: when the target is missing or malformed.
    """
    fqdn = (settings.azure_sql_server_fqdn or "").strip().lower()
    if not fqdn:
        raise UnsafeDatabaseTargetError("AZURE_SQL_SERVER_FQDN is not set.")
    if not fqdn.endswith(_AZURE_SQL_SUFFIX):
        raise UnsafeDatabaseTargetError(f"AZURE_SQL_SERVER_FQDN must end with '{_AZURE_SQL_SUFFIX}'; got '{fqdn}'.")

    derived_server = fqdn.removesuffix(_AZURE_SQL_SUFFIX)
    if not _SERVER_NAME_PATTERN.match(derived_server):
        raise UnsafeDatabaseTargetError(f"'{derived_server}' is not a valid Azure SQL server name.")

    configured_server = (settings.azure_sql_server_name or "").strip().lower()
    if configured_server and configured_server != derived_server:
        raise UnsafeDatabaseTargetError(
            f"AZURE_SQL_SERVER_NAME ('{configured_server}') does not match "
            f"AZURE_SQL_SERVER_FQDN ('{fqdn}'). Refusing to guess which one is correct."
        )

    database = settings.azure_sql_database_name.strip()
    if not database:
        raise UnsafeDatabaseTargetError("AZURE_SQL_DATABASE_NAME is not set.")
    if not _DATABASE_NAME_PATTERN.match(database):
        raise UnsafeDatabaseTargetError(f"'{database}' is not a valid Azure SQL database name.")

    return DatabaseTarget(
        server_name=derived_server,
        server_fqdn=fqdn,
        database_name=database,
        authentication=str(settings.azure_sql_authentication),
    )


def require_bootstrap_allowed(settings: Settings) -> None:
    """Refuse to continue unless ``ALLOW_DATABASE_BOOTSTRAP`` is explicitly true.

    The switch is opt-in per run so that a script cannot alter a database because
    a stale ``.env`` file happened to be present.

    Raises:
        UnsafeDatabaseTargetError: when the switch is not set.
    """
    if not settings.allow_database_bootstrap:
        raise UnsafeDatabaseTargetError(
            "Database bootstrap is disabled. Set ALLOW_DATABASE_BOOTSTRAP=true to proceed, "
            "and confirm the target shown above is the one you intend to modify."
        )
