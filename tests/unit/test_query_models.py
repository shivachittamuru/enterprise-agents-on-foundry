"""Query request validation, row limits, and result shaping.

Inputs are validated at the boundary, so an out-of-range limit is rejected before
a connection is opened rather than after a server round trip.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from enterprise_agents_on_foundry.database.models import (
    DatabaseHealth,
    DatabaseTarget,
    QueryRequest,
    QueryResult,
    TableInfo,
)


def test_defaults_are_conservative() -> None:
    request = QueryRequest(sql="SELECT 1")

    assert request.max_rows == 500
    assert request.timeout_seconds == 30


def test_surrounding_whitespace_is_stripped() -> None:
    assert QueryRequest(sql="  SELECT 1  ").sql == "SELECT 1"


def test_empty_sql_is_rejected() -> None:
    with pytest.raises(ValidationError):
        QueryRequest(sql="   ")


@pytest.mark.parametrize("max_rows", [0, -1, 10_001])
def test_out_of_range_row_limits_are_rejected(max_rows: int) -> None:
    with pytest.raises(ValidationError):
        QueryRequest(sql="SELECT 1", max_rows=max_rows)


@pytest.mark.parametrize("timeout_seconds", [0, -5, 301])
def test_out_of_range_timeouts_are_rejected(timeout_seconds: int) -> None:
    with pytest.raises(ValidationError):
        QueryRequest(sql="SELECT 1", timeout_seconds=timeout_seconds)


def test_unknown_fields_are_rejected() -> None:
    """A typo in a keyword argument must fail loudly, not be ignored."""
    with pytest.raises(ValidationError):
        QueryRequest(sql="SELECT 1", maxrows=10)  # type: ignore[call-arg]


def test_requests_are_immutable() -> None:
    request = QueryRequest(sql="SELECT 1")
    with pytest.raises(ValidationError):
        request.sql = "SELECT 2"  # type: ignore[misc]


def _result(rows: int = 2, *, truncated: bool = False) -> QueryResult:
    return QueryResult(
        columns=("Name", "Id"),
        rows=tuple((f"row-{index}", index) for index in range(rows)),
        truncated=truncated,
        elapsed_ms=12,
    )


def test_results_convert_to_dictionaries() -> None:
    assert _result(1).as_dicts() == [{"Name": "row-0", "Id": 0}]


def test_row_count_reflects_returned_rows() -> None:
    assert _result(3).row_count == 3


def test_truncation_is_visible_in_the_formatted_table() -> None:
    """Silently dropping rows is how a wrong answer looks correct."""
    rendered = _result(2, truncated=True).format_table()

    assert "truncated" in rendered.lower()


def test_formatted_table_honours_its_display_limit() -> None:
    rendered = _result(50).format_table(limit=5)

    assert "row-4" in rendered
    assert "row-5" not in rendered


def test_table_info_qualifies_its_name() -> None:
    table = TableInfo(schema_name="SalesLT", table_name="Product", approximate_rows=295)

    assert table.qualified_name == "SalesLT.Product"


def _target() -> DatabaseTarget:
    return DatabaseTarget(
        server_name="sql-demo",
        server_fqdn="sql-demo.database.windows.net",
        database_name="AdventureWorksLT",
        authentication="entra",
    )


def test_health_summary_states_the_outcome() -> None:
    healthy = DatabaseHealth(target=_target(), connected=True, elapsed_ms=8)
    unhealthy = DatabaseHealth(target=_target(), connected=False, elapsed_ms=0, error="login timeout")

    assert "Connected to" in healthy.summary
    assert "Could not connect" in unhealthy.summary
    assert "login timeout" in unhealthy.summary


def test_health_summary_never_contains_a_token() -> None:
    """Health output is printed in notebooks, so it must stay safe to share."""
    summary = DatabaseHealth(target=_target(), connected=True, elapsed_ms=8).summary

    assert "token" not in summary.lower()
    assert "pwd" not in summary.lower()
