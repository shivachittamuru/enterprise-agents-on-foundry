"""Read-only SQL enforcement and safe target resolution.

The legacy backend relied on a prompt instruction ("DO NOT make any DML
statements") as its only defence against destructive SQL, which is not a control
at all. This module provides a deterministic check that runs before any statement
reaches Azure SQL, independent of what a model was told.

Defence in depth still applies: the durable control is the read-only database
role granted to the agent identity. This validator exists so that an unsafe
statement fails fast and legibly, and so that later releases have one place to
wire agent-generated SQL through.

Known limitations, all deliberate. This is a lexical check, not a T-SQL parser.
The forbidden-keyword scan reads the whole statement, including string literals
and quoted identifiers, so ``WHERE Comment = 'please update me'`` and
``SELECT [Update]`` are refused although both are read-only. That direction of
error is the safe one and it is not worth a grammar to remove. Deciding whether
a keyword occupies a syntactic position that could mutate data needs parser-level
understanding, so no attempt is made to guess.
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

_LINE_COMMENT_START: Final = "--"
_BLOCK_COMMENT_START: Final = "/*"
_BLOCK_COMMENT_END: Final = "*/"

_QUOTE_CLOSERS: Final[dict[str, str]] = {"'": "'", '"': '"', "[": "]"}
"""Openers that suspend SQL syntax until their closer, with a doubled closer as the escape."""

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
    """Remove line and block comments so keywords cannot hide inside them.

    Quoted text is copied through untouched. A regex pass would read the ``--``
    in ``WHERE code = 'a--b'`` as a comment and return a truncated statement,
    and that statement, not the original, is what reaches the driver.
    """
    kept: list[str] = []
    index = 0
    while index < len(sql):
        char = sql[index]
        if char in _QUOTE_CLOSERS:
            end = _end_of_quoted(sql, index)
            kept.append(sql[index:end])
            index = end
        elif sql.startswith(_LINE_COMMENT_START, index):
            newline = sql.find("\n", index)
            index = len(sql) if newline == -1 else newline
            kept.append(" ")
        elif sql.startswith(_BLOCK_COMMENT_START, index):
            end = sql.find(_BLOCK_COMMENT_END, index + len(_BLOCK_COMMENT_START))
            index = len(sql) if end == -1 else end + len(_BLOCK_COMMENT_END)
            kept.append(" ")
        else:
            kept.append(char)
            index += 1
    return "".join(kept)


def split_statements(sql: str) -> list[str]:
    """Split on semicolons that sit outside quoted text.

    Naively splitting on ``;`` would misread ``'a;b'`` or ``[a;b]`` as two
    statements, so string literals and quoted identifiers are skipped whole. An
    unterminated quote swallows the rest of the input, which cannot smuggle a
    second statement past the keyword check that follows.
    """
    statements: list[str] = []
    current: list[str] = []
    index = 0

    while index < len(sql):
        char = sql[index]
        if char in _QUOTE_CLOSERS:
            end = _end_of_quoted(sql, index)
            current.append(sql[index:end])
            index = end
            continue
        if char == ";":
            statements.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1

    statements.append("".join(current))
    return [statement.strip() for statement in statements if statement.strip()]


def _end_of_quoted(sql: str, start: int) -> int:
    """Index just past the quoted run that opens at ``start``, or the end of input."""
    closer = _QUOTE_CLOSERS[sql[start]]
    index = start + 1
    while index < len(sql):
        if sql[index] == closer:
            if index + 1 < len(sql) and sql[index + 1] == closer:
                index += 2
                continue
            return index + 1
        index += 1
    return len(sql)


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
