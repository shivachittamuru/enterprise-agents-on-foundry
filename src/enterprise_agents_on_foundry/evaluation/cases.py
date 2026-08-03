"""What one evaluation case asserts.

The v0.1 dataset described expectations in prose: ``expected_behavior`` was a
free-text label and ``expected_sql_shape`` was an English sentence. Prose cannot
be checked, so the dataset could not fail anything.

Here an expectation is data. Every field is optional and every field that is set
is decidable without a model and without a human, so a case states only the
assertions it cares about and a runner can grade it deterministically.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from enterprise_agents_on_foundry.agents.state import AgentOutcome, GenerationDisposition
from enterprise_agents_on_foundry.database.tool import QueryStatus

__all__ = [
    "Difficulty",
    "EvaluationCase",
    "EvaluationCategory",
    "ExpectedOutcome",
]


class EvaluationCategory(StrEnum):
    """The behaviour class a case probes.

    Categories name what is being measured rather than what the question is
    about, so a report can say which capability regressed. The v0.1 categories
    (``join``, ``date``, ``unsafe_write``, and the rest) describe question shape,
    and they survive as tags.
    """

    QUERY_CORRECTNESS = "query_correctness"
    ANSWER_CORRECTNESS = "answer_correctness"
    SAFETY = "safety"
    REPAIR = "repair"
    CLARIFICATION = "clarification"
    UNSUPPORTED = "unsupported"
    EMPTY_RESULT = "empty_result"


class Difficulty(StrEnum):
    """How hard the case is expected to be, for reporting only."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


_NON_EXECUTING_OUTCOMES = frozenset(
    {AgentOutcome.CLARIFICATION_REQUIRED, AgentOutcome.UNSUPPORTED},
)
"""Outcomes reached before any statement exists, so no SQL evidence can be asserted."""

_OUTCOME_STATUSES: dict[AgentOutcome, frozenset[QueryStatus]] = {
    AgentOutcome.SUCCEEDED: frozenset({QueryStatus.SUCCESS}),
    AgentOutcome.EMPTY: frozenset({QueryStatus.EMPTY}),
    AgentOutcome.REJECTED: frozenset({QueryStatus.REJECTED}),
    AgentOutcome.FAILED: frozenset({QueryStatus.FAILED, QueryStatus.REJECTED}),
    AgentOutcome.CLARIFICATION_REQUIRED: frozenset(),
    AgentOutcome.UNSUPPORTED: frozenset(),
}
"""Which query status each outcome can carry, so a case cannot assert an impossible pair."""

_DISPOSITION_OUTCOMES: dict[GenerationDisposition, frozenset[AgentOutcome]] = {
    GenerationDisposition.READY: frozenset(AgentOutcome) - _NON_EXECUTING_OUTCOMES,
    GenerationDisposition.CLARIFICATION_REQUIRED: frozenset({AgentOutcome.CLARIFICATION_REQUIRED}),
    GenerationDisposition.UNSUPPORTED: frozenset({AgentOutcome.UNSUPPORTED}),
}


class ExpectedOutcome(BaseModel):
    """The assertions one case makes.

    Everything is optional. A safety case asserts a refusal and nothing else; a
    join case asserts tables and columns and says nothing about latency. Absent
    means "not asserted", never "asserted to be zero", so a case cannot fail on
    something it never claimed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    disposition: GenerationDisposition | None = Field(
        default=None,
        description="What the model must decide it can do with the question.",
    )
    outcome: AgentOutcome | None = Field(default=None, description="The terminal outcome the caller must receive.")
    query_status: QueryStatus | None = Field(default=None, description="What must happen at the database boundary.")

    required_sql_patterns: tuple[str, ...] = Field(
        default=(),
        description="Regular expressions that must match the generated SQL, case-insensitively.",
    )
    forbidden_sql_patterns: tuple[str, ...] = Field(
        default=(),
        description="Regular expressions that must not match the generated SQL. Vacuously true when no SQL exists.",
    )
    required_tables: tuple[str, ...] = Field(
        default=(),
        description="Qualified table names the statement must reference.",
    )
    expected_columns: tuple[str, ...] = Field(
        default=(),
        description="Column names the result set must contain.",
    )
    min_rows: int | None = Field(default=None, ge=0, description="Fewest rows a correct answer returns.")
    max_rows: int | None = Field(default=None, ge=0, description="Most rows a correct answer returns.")

    required_answer_text: tuple[str, ...] = Field(
        default=(),
        description="Substrings the final answer must contain, compared case-insensitively.",
    )
    forbidden_answer_text: tuple[str, ...] = Field(
        default=(),
        description="Substrings the final answer must not contain.",
    )

    min_repairs: int | None = Field(default=None, ge=0, description="Fewest repair attempts the case expects.")
    max_repairs: int | None = Field(default=None, ge=0, description="Most repair attempts the case tolerates.")
    database_calls: int | None = Field(default=None, ge=0, description="Exact number of query-tool calls expected.")
    max_model_calls: int | None = Field(default=None, ge=1, description="Model-call budget for the case.")
    max_latency_ms: float | None = Field(default=None, gt=0, description="Optional end-to-end latency budget.")

    @field_validator("required_sql_patterns", "forbidden_sql_patterns")
    @classmethod
    def _patterns_compile(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject an unparsable expression while loading rather than while grading."""
        for pattern in value:
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as error:
                raise ValueError(f"invalid regular expression {pattern!r}: {error}") from error
        return value

    @model_validator(mode="after")
    def _assertions_are_consistent(self) -> ExpectedOutcome:
        """Reject a case that could never pass, whatever the agent does."""
        if self.disposition is None and self.outcome is None:
            raise ValueError("expected must assert at least a disposition or an outcome")

        if self.min_rows is not None and self.max_rows is not None and self.min_rows > self.max_rows:
            raise ValueError("min_rows must not exceed max_rows")
        if self.min_repairs is not None and self.max_repairs is not None and self.min_repairs > self.max_repairs:
            raise ValueError("min_repairs must not exceed max_repairs")

        overlap = set(self.required_sql_patterns) & set(self.forbidden_sql_patterns)
        if overlap:
            raise ValueError(f"sql pattern is both required and forbidden: {sorted(overlap)}")
        overlap = {text.casefold() for text in self.required_answer_text} & {
            text.casefold() for text in self.forbidden_answer_text
        }
        if overlap:
            raise ValueError(f"answer text is both required and forbidden: {sorted(overlap)}")

        if self.disposition is not None and self.outcome is not None:
            allowed = _DISPOSITION_OUTCOMES[self.disposition]
            if self.outcome not in allowed:
                raise ValueError(f"outcome {self.outcome.value} cannot follow disposition {self.disposition.value}")

        if self.outcome is not None and self.query_status is not None:
            statuses = _OUTCOME_STATUSES[self.outcome]
            if self.query_status not in statuses:
                raise ValueError(
                    f"query_status {self.query_status.value} cannot accompany outcome {self.outcome.value}"
                )

        self._reject_impossible_sql_evidence()

        if self.outcome is AgentOutcome.EMPTY and (self.min_rows or self.max_rows):
            raise ValueError("an empty outcome cannot expect rows")
        if self.outcome is AgentOutcome.SUCCEEDED and self.max_rows == 0:
            raise ValueError("a succeeded outcome cannot expect zero rows")
        return self

    def _reject_impossible_sql_evidence(self) -> None:
        """Refuse row and statement assertions on a case that never reaches the database."""
        non_executing = self.outcome in _NON_EXECUTING_OUTCOMES or self.disposition in {
            GenerationDisposition.CLARIFICATION_REQUIRED,
            GenerationDisposition.UNSUPPORTED,
        }
        if not non_executing:
            return
        if self.required_sql_patterns or self.required_tables or self.expected_columns:
            raise ValueError("a clarification or unsupported case cannot require SQL, tables, or columns")
        if self.min_rows is not None or self.max_rows is not None:
            raise ValueError("a clarification or unsupported case cannot expect rows")
        if self.database_calls:
            raise ValueError("a clarification or unsupported case cannot expect database calls")


class EvaluationCase(BaseModel):
    """One question and what a correct agent does with it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", description="Stable identifier, unique within a dataset.")
    category: EvaluationCategory
    difficulty: Difficulty = Difficulty.MEDIUM
    question: str = Field(min_length=1)
    expected: ExpectedOutcome
    tags: tuple[str, ...] = Field(default=(), description="Free-form labels used to select a subset of the dataset.")
    notes: str = Field(default="", description="Why the case exists and the failure mode it targets.")

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        """Reject a question that is only whitespace."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("question must not be blank")
        return stripped

    @field_validator("tags")
    @classmethod
    def _normalize_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Fold tags to lower case so selection never depends on how a case was typed."""
        return tuple(tag.strip().casefold() for tag in value if tag.strip())
