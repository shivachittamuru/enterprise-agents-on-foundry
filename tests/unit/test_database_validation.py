"""Read-only SQL validation and safe database-target resolution."""

from __future__ import annotations

import pytest

from enterprise_agents_on_foundry.setup import database
from enterprise_agents_on_foundry.setup.config import Settings
from enterprise_agents_on_foundry.setup.database import (
    REQUIRED_ODBC_DRIVER,
    DatabaseTargetError,
    ReadOnlySqlError,
    assert_read_only_sql,
    is_read_only_sql,
    resolve_database_target,
    split_statements,
    strip_sql_comments,
)

ALLOWED_QUERIES = [
    "SELECT 1",
    "select TOP (10) Name from SalesLT.Product",
    "SELECT c.CompanyName FROM SalesLT.Customer AS c WHERE c.CompanyName LIKE 'A%'",
    "WITH totals AS (SELECT CustomerID, SUM(TotalDue) AS t FROM SalesLT.SalesOrderHeader GROUP BY CustomerID)"
    " SELECT TOP (5) * FROM totals ORDER BY t DESC",
    "-- inventory\nSELECT name FROM sys.tables",
    "/* block */ SELECT 1",
    "SELECT 1;",
    "SELECT * FROM sys.tables; -- trailing comment",
]

REJECTED_QUERIES = [
    ("", "empty"),
    ("   \n  ", "whitespace only"),
    ("DROP TABLE SalesLT.Product", "drop"),
    ("DELETE FROM SalesLT.Product", "delete"),
    ("UPDATE SalesLT.Product SET ListPrice = 0", "update"),
    ("INSERT INTO SalesLT.Product (Name) VALUES ('x')", "insert"),
    ("TRUNCATE TABLE SalesLT.Product", "truncate"),
    ("ALTER TABLE SalesLT.Product ADD col INT", "alter"),
    ("CREATE TABLE t (id INT)", "create"),
    ("EXEC sp_who", "exec"),
    ("EXECUTE sp_configure", "execute"),
    ("GRANT SELECT ON SalesLT.Product TO public", "grant"),
    ("SELECT * INTO copied FROM SalesLT.Product", "select into"),
    ("SELECT 1; DROP TABLE SalesLT.Product", "stacked statements"),
    ("SELECT 1; SELECT 2", "multiple statements"),
    ("WAITFOR DELAY '00:00:10'", "waitfor"),
    ("SELECT * FROM OPENROWSET('x', 'y', 'z')", "openrowset"),
    ("SHUTDOWN", "shutdown"),
    ("DBCC FREEPROCCACHE", "dbcc"),
    ("SELECT * FROM sp_helptext", "sp_ prefix"),
    ("SELECT * FROM xp_cmdshell", "xp_ prefix"),
    ("MERGE INTO t USING s ON 1=1 WHEN MATCHED THEN DELETE", "merge"),
]


@pytest.mark.parametrize("sql", ALLOWED_QUERIES)
def test_read_only_queries_are_accepted(sql: str) -> None:
    assert is_read_only_sql(sql) is True
    assert assert_read_only_sql(sql)


@pytest.mark.parametrize(("sql", "label"), REJECTED_QUERIES, ids=[label for _, label in REJECTED_QUERIES])
def test_unsafe_queries_are_rejected(sql: str, label: str) -> None:
    assert is_read_only_sql(sql) is False
    with pytest.raises(ReadOnlySqlError):
        assert_read_only_sql(sql)


def test_forbidden_keyword_hidden_in_a_comment_is_still_rejected() -> None:
    """Comments are stripped first, so the statement behind them is what counts."""
    with pytest.raises(ReadOnlySqlError):
        assert_read_only_sql("/* SELECT */ DROP TABLE t")


def test_comment_only_input_is_rejected() -> None:
    with pytest.raises(ReadOnlySqlError):
        assert_read_only_sql("-- just a comment")


def test_semicolon_inside_a_string_literal_is_not_a_statement_separator() -> None:
    sql = "SELECT * FROM SalesLT.Customer WHERE CompanyName = 'Bikes; Parts'"
    assert is_read_only_sql(sql) is True
    assert len(split_statements(sql)) == 1


def test_escaped_quote_inside_a_string_literal_is_handled() -> None:
    sql = "SELECT * FROM SalesLT.Customer WHERE LastName = 'O''Brien; Ltd'"
    assert len(split_statements(sql)) == 1
    assert is_read_only_sql(sql) is True


def test_strip_sql_comments_removes_both_styles() -> None:
    stripped = strip_sql_comments("SELECT 1 -- line\n/* block */, 2")
    assert "line" not in stripped
    assert "block" not in stripped
    assert "SELECT 1" in stripped


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "azure_sql_server_fqdn": "sql-eaof-dev-ab12cd.database.windows.net",
        "azure_sql_server_name": "sql-eaof-dev-ab12cd",
        "azure_sql_database_name": "AdventureWorksLT",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_valid_target_resolves() -> None:
    target = resolve_database_target(_settings())

    assert target.server_name == "sql-eaof-dev-ab12cd"
    assert target.server_fqdn == "sql-eaof-dev-ab12cd.database.windows.net"
    assert target.database_name == "AdventureWorksLT"
    assert target.authentication == "entra"


def test_connection_string_never_contains_a_password() -> None:
    connection_string = resolve_database_target(_settings()).odbc_connection_string()

    lowered = connection_string.lower()
    assert "pwd=" not in lowered
    assert "password" not in lowered
    assert "uid=" not in lowered
    assert "encrypt=yes" in lowered
    assert "trustservercertificate=no" in lowered


def test_connection_string_requests_the_supported_driver() -> None:
    """Driver 17 predates several Entra authentication modes."""
    connection_string = resolve_database_target(_settings()).odbc_connection_string()

    assert f"Driver={{{REQUIRED_ODBC_DRIVER}}}" in connection_string
    assert REQUIRED_ODBC_DRIVER == "ODBC Driver 18 for SQL Server"


def test_missing_driver_explains_how_to_install_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare pyodbc IM002 does not say which driver is wanted."""
    monkeypatch.setattr(database, "installed_odbc_drivers", lambda: ("SQL Server",))

    with pytest.raises(DatabaseTargetError) as error:
        database.assert_odbc_driver_available()

    message = str(error.value)
    assert REQUIRED_ODBC_DRIVER in message
    assert "msodbcsql18" in message
    assert "SQL Server" in message


def test_present_driver_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(database, "installed_odbc_drivers", lambda: (REQUIRED_ODBC_DRIVER,))

    database.assert_odbc_driver_available()


def test_display_is_safe_to_log() -> None:
    assert resolve_database_target(_settings()).display == (
        "sql-eaof-dev-ab12cd.database.windows.net/AdventureWorksLT (auth=entra)"
    )


def test_missing_fqdn_is_rejected() -> None:
    with pytest.raises(DatabaseTargetError, match="AZURE_SQL_SERVER_FQDN"):
        resolve_database_target(_settings(azure_sql_server_fqdn=None))


def test_non_azure_host_is_rejected() -> None:
    """A hostname outside Azure SQL suggests a misdirected connection."""
    with pytest.raises(DatabaseTargetError, match=r"database\.windows\.net"):
        resolve_database_target(_settings(azure_sql_server_fqdn="evil.example.com", azure_sql_server_name=None))


def test_conflicting_server_name_and_fqdn_are_rejected() -> None:
    with pytest.raises(DatabaseTargetError, match="Refusing to guess"):
        resolve_database_target(_settings(azure_sql_server_name="some-other-server"))


def test_malformed_database_name_is_rejected() -> None:
    with pytest.raises(DatabaseTargetError):
        resolve_database_target(_settings(azure_sql_database_name="bad;name"))
