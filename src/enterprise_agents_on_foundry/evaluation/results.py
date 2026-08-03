"""What one graded case leaves behind.

A pass or fail on its own is not evidence: it cannot be re-checked, compared
across releases, or explained to anyone who did not watch the run. So the result
carries the whole observable trace of the case, including the numbers the
roadmap's permanent dimensions ask for.

Every field here is safe to write to disk. No connection string, token, or
credential reaches this module, and exception text is truncated rather than
attached whole.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from enterprise_agents_on_foundry.agents.state import AgentOutcome, GenerationDisposition
from enterprise_agents_on_foundry.database.tool import QueryStatus
from enterprise_agents_on_foundry.evaluation.cases import EvaluationCategory, ExpectedOutcome

MAX_ERROR_MESSAGE_CHARS = 500
"""Enough to identify a failure, short enough that nothing long is copied wholesale."""

__all__ = [
    "MAX_ERROR_MESSAGE_CHARS",
    "CaseResult",
    "ErrorInfo",
    "EvaluationFailureCode",
    "TokenUsage",
]


class EvaluationFailureCode(StrEnum):
    """Why a case failed, named precisely enough to act on.

    One code per assertion the expectation can make, so a report groups failures
    by cause rather than by question. "The agent got it wrong" is not a finding.
    """

    DISPOSITION_MISMATCH = "disposition_mismatch"
    OUTCOME_MISMATCH = "outcome_mismatch"
    QUERY_STATUS_MISMATCH = "query_status_mismatch"
    MISSING_SQL = "missing_sql"
    MISSING_SQL_PATTERN = "missing_sql_pattern"
    FORBIDDEN_SQL_PATTERN = "forbidden_sql_pattern"
    MISSING_TABLE = "missing_table"
    MISSING_COLUMN = "missing_column"
    TOO_FEW_ROWS = "too_few_rows"
    TOO_MANY_ROWS = "too_many_rows"
    MISSING_ANSWER = "missing_answer"
    MISSING_ANSWER_TEXT = "missing_answer_text"
    FORBIDDEN_ANSWER_TEXT = "forbidden_answer_text"
    MISSING_CLARIFICATION = "missing_clarification"
    TOO_FEW_REPAIRS = "too_few_repairs"
    TOO_MANY_REPAIRS = "too_many_repairs"
    DATABASE_CALL_COUNT_MISMATCH = "database_call_count_mismatch"
    MODEL_CALL_BUDGET_EXCEEDED = "model_call_budget_exceeded"
    LATENCY_BUDGET_EXCEEDED = "latency_budget_exceeded"
    UNSAFE_STATEMENT_EXECUTED = "unsafe_statement_executed"
    AGENT_ERROR = "agent_error"
    HARNESS_ERROR = "harness_error"


class TokenUsage(BaseModel):
    """Tokens a run consumed, as far as the provider reported them.

    Absent counts stay ``None``. A zero would claim a measurement that was never
    taken, and a fake model reports nothing at all.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class ErrorInfo(BaseModel):
    """A sanitized exception, kept for diagnosis rather than for replay."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str
    message: str = ""

    @classmethod
    def from_exception(cls, error: BaseException) -> ErrorInfo:
        """Record the type and a truncated, single-line message."""
        message = " ".join(str(error).split())
        if len(message) > MAX_ERROR_MESSAGE_CHARS:
            message = f"{message[:MAX_ERROR_MESSAGE_CHARS]}..."
        return cls(type=type(error).__name__, message=message)


class CaseResult(BaseModel):
    """The graded evidence for one case."""

    # model_calls would otherwise collide with pydantic's protected 'model_' namespace.
    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    case_id: str
    category: EvaluationCategory
    expected: ExpectedOutcome
    passed: bool
    failure_codes: tuple[EvaluationFailureCode, ...] = ()
    failure_details: tuple[str, ...] = ()

    disposition: GenerationDisposition | None = None
    outcome: AgentOutcome | None = None
    query_status: QueryStatus | None = None
    sql: str | None = None
    validation_passed: bool | None = None
    validation_error: str | None = None

    repair_count: int = Field(default=0, ge=0)
    database_calls: int = Field(default=0, ge=0)
    columns: tuple[str, ...] = ()
    row_count: int | None = Field(default=None, ge=0)
    answer: str | None = None
    clarification_question: str | None = None

    model_calls: int = Field(default=0, ge=0)
    token_usage: TokenUsage | None = None
    stage_latencies_ms: dict[str, float] = Field(default_factory=dict)
    total_latency_ms: float | None = Field(default=None, ge=0)
    error: ErrorInfo | None = None

    @model_validator(mode="after")
    def _verdict_matches_codes(self) -> CaseResult:
        """Keep the verdict and the codes from disagreeing.

        A pass carrying failure codes, or a failure carrying none, would make the
        summary and the detail tell different stories.
        """
        if self.passed and self.failure_codes:
            raise ValueError("a passing result must carry no failure codes")
        if not self.passed and not self.failure_codes:
            raise ValueError("a failing result must carry at least one failure code")
        return self
