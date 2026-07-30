"""The query tool contract the agent executes through.

v0.3 gave the graph a bare ``Callable[[QueryRequest], QueryResult]`` and let it
signal refusal and outage by raising. That made the two most interesting
outcomes invisible in the type, and it left "the query ran and matched nothing"
indistinguishable from "the query ran and matched something".

Here execution is a tool with a declared input and a declared output. Expected
outcomes are values; only a programming error still raises.

This is a boundary, not a general-purpose tool. The model cannot call it, cannot
choose it, and cannot supply anything to it that has not already passed
:func:`assert_read_only_sql`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from enterprise_agents_on_foundry.database.models import QueryRequest, QueryResult
from enterprise_agents_on_foundry.errors import DatabaseConnectionError, QueryValidationError
from enterprise_agents_on_foundry.observability.timing import timed

__all__ = [
    "QueryStatus",
    "QueryTool",
    "QueryToolResult",
    "ReadOnlyQueryTool",
]


class QueryStatus(StrEnum):
    """What happened to one statement at the database boundary."""

    SUCCESS = "success"
    EMPTY = "empty"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class QueryToolResult:
    """The outcome of one tool call.

    Exactly one of ``result`` and ``error`` is populated, so a caller cannot read
    rows from a refusal or a reason from a success.
    """

    status: QueryStatus
    result: QueryResult | None = None
    error: str | None = None
    elapsed_ms: float = 0.0

    def __post_init__(self) -> None:
        """Refuse a result that contradicts its own status.

        Fakes implement this contract too, so the invariant is checked here
        rather than trusted at each construction site.
        """
        if self.status in {QueryStatus.SUCCESS, QueryStatus.EMPTY}:
            if self.result is None:
                raise ValueError(f"a {self.status.value} result must carry rows")
            if (self.status is QueryStatus.EMPTY) != (self.result.row_count == 0):
                raise ValueError(f"a {self.status.value} result does not match its row count")
        elif self.result is not None or not self.error:
            raise ValueError(f"a {self.status.value} result must carry a reason and no rows")

    @property
    def row_count(self) -> int:
        """Rows returned, after truncation. Zero when nothing ran."""
        return self.result.row_count if self.result is not None else 0


class QueryTool(Protocol):
    """One read-only query, in and out."""

    def execute(self, request: QueryRequest) -> QueryToolResult:
        """Run one already-validated request and report what happened."""
        ...


@dataclass(frozen=True, slots=True)
class ReadOnlyQueryTool:
    """The production tool, over :meth:`DatabaseClient.execute`.

    The client is wrapped rather than replaced, so read-only validation, the row
    cap, the timeout, and Microsoft Entra authentication are still applied by the
    boundary that already owned them. This adds no new way to reach the driver.
    """

    run: Callable[[QueryRequest], QueryResult]

    def execute(self, request: QueryRequest) -> QueryToolResult:
        """Translate the database boundary's outcomes into typed statuses."""
        with timed() as stopwatch:
            try:
                result = self.run(request)
            except QueryValidationError as error:
                return QueryToolResult(
                    status=QueryStatus.REJECTED,
                    error=str(error),
                    elapsed_ms=stopwatch.milliseconds,
                )
            except DatabaseConnectionError as error:
                return QueryToolResult(
                    status=QueryStatus.FAILED,
                    error=str(error),
                    elapsed_ms=stopwatch.milliseconds,
                )

        status = QueryStatus.SUCCESS if result.row_count else QueryStatus.EMPTY
        return QueryToolResult(status=status, result=result, elapsed_ms=stopwatch.milliseconds)
