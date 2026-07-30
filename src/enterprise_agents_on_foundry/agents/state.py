"""Typed contracts for the Text-to-SQL graph.

The legacy agent used LangGraph's ``MessagesState`` and kept every fact in a
message transcript, then deleted messages to stop the transcript growing. Its
real state was therefore implicit, and no routing decision could be tested
without a model.

Here the state is explicit. Every field a routing rule reads is a named key with
a declared type, so the whole control flow is exercisable with plain
dictionaries.

Three shapes are deliberately separate:

``AgentInput``
    What a caller may supply. Validated, because it can come from a CLI
    argument or a notebook.
``AgentState``
    Internal working state. Not part of the public contract.
``AgentOutput``
    What a caller receives. A frozen dataclass built by exactly one node per
    outcome, so success and failure always have the same shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

from enterprise_agents_on_foundry.database.models import QueryResult

MAX_REPAIR_ATTEMPTS: Final = 1
"""One repair, not a loop.

The legacy graph routed the model back to itself whenever it emitted a tool
call, with no attempt counter and no recursion limit on invoke, so a model that
kept producing broken SQL ran until something else stopped it. Bounding the
count in state means the maximum path length is fixed when the graph compiles.
"""

DEFAULT_MAX_ROWS: Final = 200
"""Row cap applied by ``QueryRequest``, not by asking the model nicely.

The legacy prompt requested "at most 50 results" and nothing enforced it.
"""

__all__ = [
    "DEFAULT_MAX_ROWS",
    "MAX_REPAIR_ATTEMPTS",
    "AgentInput",
    "AgentOutput",
    "AgentState",
    "AgentStatus",
    "FailureStage",
    "NodeName",
    "SqlDraft",
    "initial_state",
    "to_output",
]


class NodeName(StrEnum):
    """Every node in the graph.

    An enumeration rather than bare strings, so a typo in an edge definition is
    a name error at import time instead of a routing bug at run time.
    """

    LOAD_SCHEMA = "load_schema"
    GENERATE_SQL = "generate_sql"
    VALIDATE_SQL = "validate_sql"
    EXECUTE_SQL = "execute_sql"
    REPAIR_SQL = "repair_sql"
    COMPOSE_ANSWER = "compose_answer"
    FAIL = "fail"


class AgentStatus(StrEnum):
    """Terminal outcome of one question."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class FailureStage(StrEnum):
    """Which boundary refused, so a failure is diagnosable without a transcript."""

    GENERATION = "generation"
    VALIDATION = "validation"
    EXECUTION = "execution"
    ANSWER = "answer"


class SqlDraft(BaseModel):
    """The only thing the model is allowed to produce for a query.

    The model returns a statement and a reason. It does not return a decision, a
    tool call, or an action. Execution is the graph's job, not the model's.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sql: str = Field(min_length=1, description="A single read-only T-SQL SELECT statement.")
    rationale: str = Field(default="", description="One line explaining the query. Never executed.")

    @field_validator("sql")
    @classmethod
    def _strip_sql(cls, value: str) -> str:
        """Trim whitespace and reject a blank statement.

        Models frequently wrap SQL in a fenced code block. Fence removal belongs
        in the model boundary rather than here, so this validator only rejects a
        statement that carries no content at all.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("sql must not be blank")
        return stripped


class AgentInput(BaseModel):
    """What a caller may supply."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str = Field(min_length=1)
    max_rows: int = Field(default=DEFAULT_MAX_ROWS, ge=1, le=10_000)

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        """Reject a question that is only whitespace."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("question must not be blank")
        return stripped


class AgentState(TypedDict):
    """Internal working state.

    Every key is always present. Absence is expressed as ``None`` rather than a
    missing key, so a routing rule never has to distinguish "not set yet" from
    "set to nothing", and ``total=False`` guesswork does not leak into tests.

    No reducers are declared. Nothing here accumulates: a repaired statement
    replaces the failed one rather than being appended to a history.
    """

    question: str
    max_rows: int
    schema_context: str
    sql: str | None
    rationale: str | None
    validation_error: str | None
    execution_error: str | None
    repair_attempts: int
    result: QueryResult | None
    answer: str | None
    status: AgentStatus | None
    failure_stage: FailureStage | None
    failure_reason: str | None


@dataclass(frozen=True, slots=True)
class AgentOutput:
    """What a caller receives.

    Built by ``compose_answer`` or by ``fail`` and by nothing else, so every
    failure path produces the same fields.
    """

    status: AgentStatus
    question: str
    answer: str | None = None
    sql: str | None = None
    row_count: int | None = None
    repair_attempts: int = 0
    failure_stage: FailureStage | None = None
    failure_reason: str | None = None

    @property
    def succeeded(self) -> bool:
        """True when the question was answered."""
        return self.status is AgentStatus.SUCCEEDED


def initial_state(request: AgentInput) -> AgentState:
    """Build the starting state for one question.

    Written once here rather than at each call site, so a new field cannot be
    forgotten by a caller and then read as missing by a node.
    """
    return AgentState(
        question=request.question,
        max_rows=request.max_rows,
        schema_context="",
        sql=None,
        rationale=None,
        validation_error=None,
        execution_error=None,
        repair_attempts=0,
        result=None,
        answer=None,
        status=None,
        failure_stage=None,
        failure_reason=None,
    )


def to_output(state: AgentState) -> AgentOutput:
    """Translate terminal state into the caller-facing result.

    Written once so a success and a failure cannot drift into different shapes,
    and so a caller never reads internal state directly. A state that reaches
    here without a status is reported as a failure rather than assumed to have
    succeeded.
    """
    status = state["status"] or AgentStatus.FAILED
    result = state["result"]
    return AgentOutput(
        status=status,
        question=state["question"],
        answer=state["answer"],
        sql=state["sql"],
        row_count=result.row_count if result is not None else None,
        repair_attempts=state["repair_attempts"],
        failure_stage=state["failure_stage"],
        failure_reason=state["failure_reason"],
    )
