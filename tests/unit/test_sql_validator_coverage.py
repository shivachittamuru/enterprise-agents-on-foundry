"""Table-driven coverage of the read-only SQL validator and the row cap.

The validator is the deterministic control that stands between model output and
the database, so its behaviour is enumerated rather than sampled. Cases that are
refused although they are harmless are listed too: over-refusal is the safe
direction, and pinning it stops a later change from quietly widening what gets
through.
"""

from __future__ import annotations

from typing import Any

import pytest

from enterprise_agents_on_foundry.config.settings import Settings
from enterprise_agents_on_foundry.database.connection import DatabaseClient
from enterprise_agents_on_foundry.database.models import QueryRequest
from enterprise_agents_on_foundry.database.validation import (
    assert_read_only_sql,
    is_read_only_sql,
    resolve_database_target,
    split_statements,
    strip_sql_comments,
)
from enterprise_agents_on_foundry.errors import QueryValidationError

ACCEPTED: list[tuple[str, str]] = [
    ("SELECT 1", "minimal select"),
    ("SELECT Name, ListPrice FROM SalesLT.Product", "column list"),
    ("SELECT TOP (10) Name FROM SalesLT.Product ORDER BY ListPrice DESC", "top and order by"),
    (
        "SELECT p.Name, c.Name FROM SalesLT.Product AS p"
        " JOIN SalesLT.ProductCategory AS c ON c.ProductCategoryID = p.ProductCategoryID",
        "join",
    ),
    ("SELECT COUNT(*) FROM SalesLT.Product WHERE ListPrice > 0", "aggregate with filter"),
    ("WITH totals AS (SELECT CustomerID FROM SalesLT.SalesOrderHeader) SELECT * FROM totals", "single cte"),
    (
        "WITH a AS (SELECT 1 AS x), b AS (SELECT 2 AS y) SELECT a.x, b.y FROM a CROSS JOIN b",
        "chained ctes",
    ),
    ("WITH r AS (SELECT 1 AS n) SELECT n FROM r", "cte reading only"),
    ("select top (5) name from saleslt.product", "all lower case"),
    ("SELECT TOP (5) NAME FROM SALESLT.PRODUCT", "all upper case"),
    ("SeLeCt 1", "mixed case"),
    ("   SELECT 1   ", "surrounding whitespace"),
    ("\n\tSELECT\n\t1\n", "newlines and tabs"),
    ("SELECT\n  Name,\n  ListPrice\nFROM SalesLT.Product", "multi-line statement"),
    ("SELECT 1;", "one trailing semicolon"),
    ("SELECT 1 ;  \n", "trailing semicolon with whitespace"),
    ("SELECT 1;;", "repeated trailing semicolons"),
    ("-- inventory\nSELECT Name FROM SalesLT.Product", "leading line comment"),
    ("SELECT Name FROM SalesLT.Product -- trailing note", "trailing line comment"),
    ("/* block */ SELECT 1", "leading block comment"),
    ("SELECT /* inline */ Name FROM SalesLT.Product", "inline block comment"),
    ("SELECT 1 /* multi\nline\ncomment */", "multi-line block comment"),
    ("-- do not DELETE anything\nSELECT 1", "forbidden word only inside a comment"),
    ("SELECT Name FROM SalesLT.Product WHERE Color = 'red--ish'", "double dash inside a literal"),
    ("SELECT Name FROM SalesLT.Product WHERE Color = 'a/*b*/c'", "block markers inside a literal"),
    ("SELECT CompanyName FROM SalesLT.Customer WHERE CompanyName = 'Bikes; Parts'", "semicolon inside a literal"),
    ("SELECT LastName FROM SalesLT.Customer WHERE LastName = 'O''Brien; Ltd'", "escaped quote inside a literal"),
    ("SELECT [Order; Total] FROM SalesLT.SalesOrderHeader", "semicolon inside a bracketed identifier"),
    ('SELECT "Order; Total" FROM SalesLT.SalesOrderHeader', "semicolon inside a quoted identifier"),
]

REJECTED: list[tuple[str, str]] = [
    ("", "empty input"),
    ("    \n\t ", "whitespace only"),
    ("-- only a comment", "comment only"),
    ("/* only a comment */", "block comment only"),
    ("SELECT 1; SELECT 2", "two read-only statements"),
    ("SELECT 1;\nSELECT 2;\nSELECT 3", "three statements"),
    ("INSERT INTO SalesLT.Product (Name) VALUES ('x')", "insert"),
    ("UPDATE SalesLT.Product SET ListPrice = 0", "update"),
    ("DELETE FROM SalesLT.Product", "delete"),
    ("MERGE INTO t USING s ON 1 = 1 WHEN MATCHED THEN DELETE", "merge"),
    ("DROP TABLE SalesLT.Product", "drop"),
    ("ALTER TABLE SalesLT.Product ADD col INT", "alter"),
    ("TRUNCATE TABLE SalesLT.Product", "truncate"),
    ("EXEC sp_who", "exec"),
    ("EXECUTE dbo.SomeProcedure", "execute"),
    ("SELECT * INTO copied FROM SalesLT.Product", "select into"),
    ("SELECT Name INTO #temp FROM SalesLT.Product", "select into a temp table"),
    ("SELECT 1; DELETE FROM SalesLT.Product", "mutation stacked after a query"),
    ("SELECT 1 -- harmless\n; DROP TABLE SalesLT.Product", "mutation hidden after a line comment"),
    ("SELECT 1 /* harmless */ ; TRUNCATE TABLE SalesLT.Product", "mutation hidden after a block comment"),
    ("/* SELECT */ DROP TABLE SalesLT.Product", "select present only inside a comment"),
    ("SELECT 1 --\nDELETE FROM SalesLT.Product", "mutation on the line after a comment"),
    ("WITH c AS (DELETE FROM SalesLT.Product OUTPUT deleted.Name AS Name) SELECT Name FROM c", "delete inside a cte"),
    ("WITH c AS (SELECT 1 AS n) INSERT INTO t SELECT n FROM c", "insert after a cte"),
    ("WITH c AS (SELECT 1 AS n) UPDATE SalesLT.Product SET ListPrice = 0", "update after a cte"),
    ("WITH c AS (SELECT * INTO copied FROM SalesLT.Product) SELECT 1", "select into inside a cte"),
    ("EXEC('SELECT 1')", "dynamic sql"),
    ("SELECT * FROM sp_helptext", "sp_ prefix"),
    ("SELECT * FROM xp_cmdshell", "xp_ prefix"),
    ("WAITFOR DELAY '00:00:10'", "waitfor"),
    ("SELECT * FROM OPENROWSET('x', 'y', 'z')", "openrowset"),
    ("VALUES (1)", "statement that is not select or with"),
]

CONSERVATIVE_REJECTIONS: list[tuple[str, str]] = [
    ("SELECT Name FROM SalesLT.Product WHERE Name = 'please update me'", "forbidden word inside a string literal"),
    ("SELECT Comment FROM t WHERE Comment LIKE '%drop%'", "forbidden word inside a like pattern"),
    ("SELECT [Update] FROM SalesLT.Product", "forbidden word as a bracketed identifier"),
    ('SELECT "Delete" FROM SalesLT.Product', "forbidden word as a quoted identifier"),
    ("SELECT sp_rate FROM t", "sp_ prefix on an ordinary column"),
]


@pytest.mark.parametrize(("sql", "label"), ACCEPTED, ids=[label for _, label in ACCEPTED])
def test_read_only_statements_are_accepted(sql: str, label: str) -> None:
    assert is_read_only_sql(sql) is True
    assert assert_read_only_sql(sql)


@pytest.mark.parametrize(("sql", "label"), REJECTED, ids=[label for _, label in REJECTED])
def test_unsafe_or_ambiguous_statements_are_rejected(sql: str, label: str) -> None:
    assert is_read_only_sql(sql) is False
    with pytest.raises(QueryValidationError):
        assert_read_only_sql(sql)


@pytest.mark.parametrize(
    ("sql", "label"),
    CONSERVATIVE_REJECTIONS,
    ids=[label for _, label in CONSERVATIVE_REJECTIONS],
)
def test_harmless_statements_are_still_refused_when_a_keyword_appears_anywhere(sql: str, label: str) -> None:
    """A documented limitation: the keyword scan is lexical, not syntactic.

    Telling a keyword in a literal from a keyword in a mutating position needs a
    T-SQL parser. Until one exists, refusing is the safe answer.
    """
    assert is_read_only_sql(sql) is False


def test_the_validated_statement_is_what_runs() -> None:
    """The return value reaches the driver, so it must not be mangled."""
    sql = "SELECT Name FROM SalesLT.Product WHERE Color = 'red--ish' -- note"
    statement = assert_read_only_sql(sql)

    assert "'red--ish'" in statement
    assert "note" not in statement


def test_a_trailing_semicolon_is_removed_from_the_validated_statement() -> None:
    assert assert_read_only_sql("SELECT 1;") == "SELECT 1"


def test_comments_are_stripped_but_quoted_text_is_not() -> None:
    stripped = strip_sql_comments("SELECT '-- keep', '/* keep */' -- drop\n/* drop */")

    assert "'-- keep'" in stripped
    assert "'/* keep */'" in stripped
    assert "drop" not in stripped


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1", 1),
        ("SELECT 1;", 1),
        ("SELECT 1; SELECT 2", 2),
        ("SELECT ';;;'", 1),
        ("SELECT [a;b]", 1),
        ('SELECT "a;b"', 1),
        ("SELECT 'a''b;c'", 1),
        ("SELECT 'unterminated; DROP TABLE t", 1),
    ],
)
def test_statement_splitting_ignores_quoted_semicolons(sql: str, expected: int) -> None:
    assert len(split_statements(sql)) == expected


def test_an_unterminated_quote_cannot_smuggle_a_second_statement() -> None:
    """The rest of the input is swallowed, and the keyword scan still sees it."""
    assert is_read_only_sql("SELECT 'oops; DROP TABLE SalesLT.Product") is False


class _FakeCursor:
    """A cursor that returns a fixed number of rows and records the fetch size."""

    def __init__(self, rows: int) -> None:
        self._rows = rows
        self.description = [("Name",), ("ListPrice",)]
        self.executed: str | None = None
        self.requested: int | None = None
        self.closed = False

    def execute(self, sql: str) -> None:
        self.executed = sql

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        self.requested = size
        return [(f"row-{index}", index) for index in range(min(self._rows, size))]

    def close(self) -> None:
        self.closed = True


class _FakeConnection:
    """Just enough of a pyodbc connection for the row cap to be observed."""

    def __init__(self, rows: int) -> None:
        self.cursor_instance = _FakeCursor(rows)
        self.timeout = 0

    def cursor(self) -> _FakeCursor:
        return self.cursor_instance


def _client(available_rows: int, *, max_rows: int = 500) -> tuple[DatabaseClient, _FakeConnection]:
    """A client over a fake connection holding ``available_rows`` rows."""
    settings = Settings(  # type: ignore[call-arg]
        azure_sql_server_fqdn="sql-eaof-dev-ab12cd.database.windows.net",
        azure_sql_database_name="AdventureWorksLT",
        database_max_result_rows=max_rows,
    )
    connection = _FakeConnection(available_rows)
    return DatabaseClient(resolve_database_target(settings), settings, connection), connection


def test_a_result_within_the_row_cap_is_not_truncated() -> None:
    client, _ = _client(available_rows=3)

    result = client.execute(QueryRequest(sql="SELECT Name, ListPrice FROM SalesLT.Product", max_rows=10))

    assert result.row_count == 3
    assert result.truncated is False
    assert result.columns == ("Name", "ListPrice")


def test_a_result_over_the_row_cap_is_truncated_and_says_so() -> None:
    client, connection = _client(available_rows=100)

    result = client.execute(QueryRequest(sql="SELECT Name, ListPrice FROM SalesLT.Product", max_rows=10))

    assert result.row_count == 10
    assert result.truncated is True
    # One row beyond the cap is fetched so truncation is observed, not guessed.
    assert connection.cursor_instance.requested == 11
    assert "truncated" in result.format_table()


def test_a_result_exactly_at_the_row_cap_is_not_truncated() -> None:
    client, _ = _client(available_rows=10)

    result = client.execute(QueryRequest(sql="SELECT Name, ListPrice FROM SalesLT.Product", max_rows=10))

    assert result.row_count == 10
    assert result.truncated is False


def test_the_row_cap_defaults_to_the_configured_limit() -> None:
    client, _ = _client(available_rows=50, max_rows=25)

    result = client.execute_sql("SELECT Name, ListPrice FROM SalesLT.Product")

    assert client.max_rows == 25
    assert result.row_count == 25
    assert result.truncated is True


def test_an_unsafe_statement_never_reaches_the_cursor() -> None:
    client, connection = _client(available_rows=5)

    with pytest.raises(QueryValidationError):
        client.execute(QueryRequest(sql="DELETE FROM SalesLT.Product"))

    assert connection.cursor_instance.executed is None
