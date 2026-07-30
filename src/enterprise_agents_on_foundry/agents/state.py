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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from enterprise_agents_on_foundry.database.models import QueryResult
from enterprise_agents_on_foundry.database.tool import QueryStatus

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
    "AgentOutcome",
    "AgentOutput",
    "AgentState",
    "FailureStage",
    "GenerationDisposition",
    "ModelCallMetadata",
    "NodeName",
    "SqlGenerationResult",
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
    FINALIZE = "finalize"


class AgentOutcome(StrEnum):
    """Terminal outcome of one question.

    v0.3 had two members, so "no rows matched", "the question is ambiguous",
    "this schema cannot answer it", and "the statement was refused" all reached
    the caller as the same undifferentiated failure.
    """

    SUCCEEDED = "succeeded"
    EMPTY = "empty"
    CLARIFICATION_REQUIRED = "clarification_required"
    UNSUPPORTED = "unsupported"
    REJECTED = "rejected"
    FAILED = "failed"


class FailureStage(StrEnum):
    """Which boundary refused, so a failure is diagnosable without a transcript."""

    GENERATION = "generation"
    VALIDATION = "validation"
    EXECUTION = "execution"
    ANSWER = "answer"


class GenerationDisposition(StrEnum):
    """What the model says it can do with the question."""

    READY = "ready"
    CLARIFICATION_REQUIRED = "clarification_required"
    UNSUPPORTED = "unsupported"


class SqlGenerationResult(BaseModel):
    """The only thing the model is allowed to produce for a query.

    v0.3 accepted a statement and nothing else, so a model facing an ambiguous
    or unanswerable question had guessing as its only legal move. Naming the two
    honest alternatives is what makes refusing to guess expressible.

    It is still not a decision to act. Execution remains the graph's job.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    disposition: GenerationDisposition = Field(
        description="Whether a statement is ready, the question is ambiguous, or the schema cannot answer it.",
    )
    sql: str | None = Field(
        default=None,
        description="A single read-only T-SQL SELECT statement. Required when ready, forbidden otherwise.",
    )
    rationale: str = Field(default="", description="One line explaining the disposition. Never executed.")
    clarification_question: str | None = Field(
        default=None,
        description="The single question to ask back. Required when clarification is needed.",
    )

    @field_validator("sql", "clarification_question")
    @classmethod
    def _blank_is_absent(cls, value: str | None) -> str | None:
        """Trim, and treat a whitespace-only field as absent.

        Models frequently wrap SQL in a fenced code block. Fence removal belongs
        in the model boundary rather than here, so this only settles whether the
        field carries content, which the invariants below then rely on.
        """
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def _fields_match_disposition(self) -> SqlGenerationResult:
        """Reject a response that contradicts its own disposition.

        A refusal carrying executable SQL is the dangerous case: it would let a
        statement the model declined to stand behind reach the validator anyway.
        """
        if self.disposition is GenerationDisposition.READY:
            if self.sql is None:
                raise ValueError("sql is required when disposition is ready")
        elif self.sql is not None:
            raise ValueError(f"sql must be absent when disposition is {self.disposition.value}")

        if self.disposition is GenerationDisposition.CLARIFICATION_REQUIRED and self.clarification_question is None:
            raise ValueError("clarification_question is required when disposition is clarification_required")
        return self


@dataclass(frozen=True, slots=True)
class ModelCallMetadata:
    """What one model call cost, as far as the provider reported it.

    Everything except the purpose is optional, and absent usage stays ``None``
    rather than becoming zero: a fake model has no deployment, and not every
    response carries token counts. A zero would be indistinguishable from a
    measurement that was never taken.
    """

    purpose: str
    deployment: str | None = None
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


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

    No reducers are declared. Nothing here accumulates except ``model_calls``,
    and that is appended by the node that made the call rather than merged
    behind the node's back, so the whole state stays plain data.
    """

    question: str
    max_rows: int
    schema_context: str
    disposition: GenerationDisposition | None
    sql: str | None
    rationale: str | None
    clarification_question: str | None
    validation_error: str | None
    query_status: QueryStatus | None
    execution_error: str | None
    repair_attempts: int
    result: QueryResult | None
    answer: str | None
    outcome: AgentOutcome | None
    failure_stage: FailureStage | None
    failure_reason: str | None
    model_calls: tuple[ModelCallMetadata, ...]


@dataclass(frozen=True, slots=True)
class AgentOutput:
    """What a caller receives.

    Built by ``compose_answer`` or by ``finalize`` and by nothing else, so every
    outcome arrives in the same shape and a caller branches on ``outcome``
    rather than on which fields happen to be populated.
    """

    outcome: AgentOutcome
    question: str
    answer: str | None = None
    clarification_question: str | None = None
    sql: str | None = None
    row_count: int | None = None
    query_status: QueryStatus | None = None
    repair_attempts: int = 0
    failure_stage: FailureStage | None = None
    failure_reason: str | None = None
    model_calls: tuple[ModelCallMetadata, ...] = ()

    @property
    def succeeded(self) -> bool:
        """True when the question was answered from rows that were found."""
        return self.outcome is AgentOutcome.SUCCEEDED

    @property
    def model_call_count(self) -> int:
        """How many times the model was called for this question."""
        return len(self.model_calls)


def initial_state(request: AgentInput) -> AgentState:
    """Build the starting state for one question.

    Written once here rather than at each call site, so a new field cannot be
    forgotten by a caller and then read as missing by a node.
    """
    return AgentState(
        question=request.question,
        max_rows=request.max_rows,
        schema_context="",
        disposition=None,
        sql=None,
        rationale=None,
        clarification_question=None,
        validation_error=None,
        query_status=None,
        execution_error=None,
        repair_attempts=0,
        result=None,
        answer=None,
        outcome=None,
        failure_stage=None,
        failure_reason=None,
        model_calls=(),
    )


def to_output(state: AgentState) -> AgentOutput:
    """Translate terminal state into the caller-facing result.

    Written once so that outcomes cannot drift into different shapes, and so a
    caller never reads internal state directly. A state that reaches here
    without an outcome is reported as a failure rather than assumed to have
    succeeded.
    """
    result = state["result"]
    return AgentOutput(
        outcome=state["outcome"] or AgentOutcome.FAILED,
        question=state["question"],
        answer=state["answer"],
        clarification_question=state["clarification_question"],
        sql=state["sql"],
        row_count=result.row_count if result is not None else None,
        query_status=state["query_status"],
        repair_attempts=state["repair_attempts"],
        failure_stage=state["failure_stage"],
        failure_reason=state["failure_reason"],
        model_calls=state["model_calls"],
    )
