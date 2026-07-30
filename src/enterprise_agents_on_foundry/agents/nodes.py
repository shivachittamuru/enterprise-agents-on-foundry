"""The seven node implementations.

Each node reads state, does exactly one thing, and returns only the keys it
changed. No node decides where to go next; that is the routing rules in
:mod:`enterprise_agents_on_foundry.agents.graph`.

Every external capability arrives as a callable on :class:`AgentDependencies`.
Nodes therefore import neither the model SDK nor a database driver, and a test
supplies a fake by passing a function. The legacy agent read module-level
globals initialised at import time, so importing it opened a database
connection and no part of it could be exercised offline.

The division of labour is fixed:

* the model drafts and repairs SQL, and describes a result that has already
  been retrieved;
* the graph decides what runs;
* the database boundary decides what is safe.

The model never executes anything, never chooses a tool, and never sees a
credential.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, cast

from enterprise_agents_on_foundry.agents.graph import NodeSet, compile_graph
from enterprise_agents_on_foundry.agents.model import (
    ModelInvocation,
    build_chat_model,
    draft_sql,
    model_configuration,
    write_answer,
)
from enterprise_agents_on_foundry.agents.prompts import (
    ANSWER_SYSTEM_PROMPT,
    REPAIR_SYSTEM_PROMPT,
    SQL_SYSTEM_PROMPT,
    answer_user_prompt,
    repair_user_prompt,
    sql_user_prompt,
)
from enterprise_agents_on_foundry.agents.schema_context import load_schema_context
from enterprise_agents_on_foundry.agents.state import (
    MAX_REPAIR_ATTEMPTS,
    AgentInput,
    AgentOutcome,
    AgentOutput,
    AgentState,
    FailureStage,
    GenerationDisposition,
    ModelCallMetadata,
    NodeName,
    SqlGenerationResult,
    initial_state,
    to_output,
)
from enterprise_agents_on_foundry.config.settings import Settings
from enterprise_agents_on_foundry.database.connection import DatabaseClient
from enterprise_agents_on_foundry.database.models import QueryRequest
from enterprise_agents_on_foundry.database.tool import QueryStatus, QueryTool, ReadOnlyQueryTool
from enterprise_agents_on_foundry.database.validation import assert_read_only_sql
from enterprise_agents_on_foundry.errors import QueryValidationError

SchemaLoader = Callable[[], str]
"""Returns the rendered schema. Called once per question."""

SqlDrafter = Callable[[str, str, str], ModelInvocation[SqlGenerationResult]]
"""Takes a purpose, a system prompt, and a user prompt, and returns what the model decided."""

AnswerWriter = Callable[[str, str, str], ModelInvocation[str]]
"""Takes a purpose, a system prompt, and a user prompt, and returns prose."""

QUERY_LABEL: Final = "agent"
"""Label attached to every agent query, so agent traffic is identifiable in a
result set without inspecting the SQL.
"""

RECURSION_LIMIT: Final = 3 + (MAX_REPAIR_ATTEMPTS + 1) * 3
"""Longest path through the graph, with headroom of one step.

load_schema, generate_sql, then up to two passes of validate, execute, repair,
then a terminal node. Set explicitly rather than left at the LangGraph default,
because the graph cannot loop: reaching this limit means the topology changed.
"""

__all__ = [
    "QUERY_LABEL",
    "RECURSION_LIMIT",
    "AgentDependencies",
    "answer_question",
    "production_dependencies",
    "production_nodes",
]


@dataclass(frozen=True, slots=True)
class AgentDependencies:
    """Everything the nodes need from the outside world.

    Three callables, one tool, and one number. Constructed once per run and
    passed in explicitly, so nothing is looked up from a global while the graph
    executes.
    """

    load_schema: SchemaLoader
    draft_sql: SqlDrafter
    write_answer: AnswerWriter
    query_tool: QueryTool
    query_timeout_seconds: int = 30


def production_nodes(deps: AgentDependencies) -> NodeSet:
    """Bind the real node implementations to their dependencies."""

    def load_schema(state: AgentState) -> dict[str, Any]:
        """Load the schema the model is allowed to reason about.

        A failure here is not an agent outcome. If the database is unreachable
        the whole run raises, because answering the question was never possible
        and reporting a polite failure would hide an outage.
        """
        return {"schema_context": deps.load_schema()}

    def generate_sql(state: AgentState) -> dict[str, Any]:
        """Ask the model what it can do with the question.

        A model that returns nothing usable has nothing to repair, so the repair
        budget is marked as spent and the run ends rather than asking the same
        broken model to try again.
        """
        prompt = sql_user_prompt(
            question=state["question"],
            schema_context=state["schema_context"],
            max_rows=state["max_rows"],
        )
        invocation = deps.draft_sql(NodeName.GENERATE_SQL, SQL_SYSTEM_PROMPT, prompt)
        calls = _record(state, invocation.metadata)

        if invocation.value is None:
            return _generation_failed(invocation.error) | {"model_calls": calls}

        generation = invocation.value
        return {
            "disposition": generation.disposition,
            "sql": generation.sql,
            "rationale": generation.rationale,
            "clarification_question": generation.clarification_question,
            "validation_error": None,
            "model_calls": calls,
        }

    def validate_sql(state: AgentState) -> dict[str, Any]:
        """Check the statement deterministically before anything runs it.

        This is the only gate that matters. The prompt asks for a read-only
        single statement; this decides whether it got one.
        """
        sql = state["sql"]
        if sql is None:
            reason = state["failure_reason"] or "The model did not return a statement."
            return {"validation_error": reason, "failure_stage": FailureStage.GENERATION, "failure_reason": reason}

        try:
            statement = assert_read_only_sql(sql)
        except QueryValidationError as error:
            reason = str(error)
            return {"validation_error": reason, "failure_stage": FailureStage.VALIDATION, "failure_reason": reason}

        return {"sql": statement, "validation_error": None}

    def execute_sql(state: AgentState) -> dict[str, Any]:
        """Run the validated statement through the query tool.

        The row cap comes from the request rather than from the SQL, so a model
        that ignores the instruction to limit rows still cannot return a
        million of them. The tool revalidates the statement, which means an
        unvalidated statement cannot reach the driver even if the graph is
        rewired incorrectly.
        """
        sql = state["sql"]
        if sql is None:
            return _no_statement_to_run()

        request = QueryRequest(
            sql=sql,
            max_rows=state["max_rows"],
            timeout_seconds=deps.query_timeout_seconds,
            label=QUERY_LABEL,
        )
        outcome = deps.query_tool.execute(request)
        if outcome.status in {QueryStatus.SUCCESS, QueryStatus.EMPTY}:
            return {"result": outcome.result, "query_status": outcome.status, "execution_error": None}

        reason = outcome.error or "The query did not run."
        stage = FailureStage.VALIDATION if outcome.status is QueryStatus.REJECTED else FailureStage.EXECUTION
        return {
            "result": None,
            "query_status": outcome.status,
            "execution_error": reason,
            "failure_stage": stage,
            "failure_reason": reason,
        }

    def repair_sql(state: AgentState) -> dict[str, Any]:
        """Make the one permitted correction attempt.

        The counter is incremented before the model is called, so a model that
        answers unusably still consumes the budget and the run cannot loop.
        """
        attempts = state["repair_attempts"] + 1
        reason = state["validation_error"] or state["execution_error"] or "The statement did not produce a result."
        prompt = repair_user_prompt(
            question=state["question"],
            schema_context=state["schema_context"],
            sql=state["sql"] or "",
            error=reason,
        )
        invocation = deps.draft_sql(NodeName.REPAIR_SQL, REPAIR_SYSTEM_PROMPT, prompt)
        calls = _record(state, invocation.metadata)

        if invocation.value is None:
            # The earlier errors are cleared so the run is reported as what it
            # now is: a model that could not answer, rather than the statement
            # that prompted the correction.
            return {
                "disposition": None,
                "sql": None,
                "repair_attempts": attempts,
                "validation_error": None,
                "execution_error": None,
                "query_status": None,
                "failure_stage": FailureStage.GENERATION,
                "failure_reason": invocation.error or "The model did not return a corrected statement.",
                "model_calls": calls,
            }

        generation = invocation.value
        return {
            "disposition": generation.disposition,
            "sql": generation.sql,
            "rationale": generation.rationale,
            "clarification_question": generation.clarification_question,
            "repair_attempts": attempts,
            "validation_error": None,
            "execution_error": None,
            "model_calls": calls,
        }

    def compose_answer(state: AgentState) -> dict[str, Any]:
        """Describe the retrieved rows in plain language.

        The model sees the result, never the database. Nothing it writes here
        can change what was retrieved.

        A result with no rows is still answered, because "nothing matched" is a
        true answer to the question that was asked. It is reported as its own
        outcome rather than as a success, so a caller can tell the difference
        without parsing prose.
        """
        result = state["result"]
        if result is None:
            return _answer_failed("There are no rows to describe.")

        prompt = answer_user_prompt(question=state["question"], result=result)
        invocation = deps.write_answer(NodeName.COMPOSE_ANSWER, ANSWER_SYSTEM_PROMPT, prompt)
        calls = _record(state, invocation.metadata)

        if invocation.value is None:
            return _answer_failed(invocation.error or "The model returned no answer.") | {"model_calls": calls}

        outcome = AgentOutcome.EMPTY if result.row_count == 0 else AgentOutcome.SUCCEEDED
        return {
            "answer": invocation.value,
            "outcome": outcome,
            "failure_stage": None,
            "failure_reason": None,
            "model_calls": calls,
        }

    def finalize(state: AgentState) -> dict[str, Any]:
        """Produce every terminal result that is not an answered question.

        One node, so clarification, an unsupported question, a refused statement
        and an outright failure cannot drift into different shapes. It records
        what happened rather than inventing an answer, because a plausible
        sentence built from no rows is worse than an admitted non-answer.
        """
        disposition = state["disposition"]
        if disposition is GenerationDisposition.CLARIFICATION_REQUIRED:
            return {"outcome": AgentOutcome.CLARIFICATION_REQUIRED, "answer": None}
        if disposition is GenerationDisposition.UNSUPPORTED:
            return {"outcome": AgentOutcome.UNSUPPORTED, "answer": None}

        reason = (
            state["failure_reason"]
            or state["validation_error"]
            or state["execution_error"]
            or "The question could not be answered."
        )
        stage = state["failure_stage"] or (
            FailureStage.VALIDATION if state["validation_error"] else FailureStage.EXECUTION
        )
        refused = state["validation_error"] is not None or state["query_status"] is QueryStatus.REJECTED
        return {
            "outcome": AgentOutcome.REJECTED if refused else AgentOutcome.FAILED,
            "failure_stage": stage,
            "failure_reason": reason,
            "answer": None,
        }

    return NodeSet(
        load_schema=load_schema,
        generate_sql=generate_sql,
        validate_sql=validate_sql,
        execute_sql=execute_sql,
        repair_sql=repair_sql,
        compose_answer=compose_answer,
        finalize=finalize,
    )


def production_dependencies(settings: Settings, client: DatabaseClient) -> AgentDependencies:
    """Wire the real model client and database client into the node contracts.

    Called once, at the edge. The schema is read on each run rather than cached
    across runs, so a session cannot answer from a schema that has since
    changed. It is one query against system views, not a per-question cost that
    grows with the conversation.
    """
    config = model_configuration(settings)
    model = build_chat_model(config)

    def load_schema() -> str:
        return load_schema_context(client)

    def draft(purpose: str, system: str, user: str) -> ModelInvocation[SqlGenerationResult]:
        return draft_sql(model, config, purpose=purpose, system=system, user=user)

    def answer(purpose: str, system: str, user: str) -> ModelInvocation[str]:
        return write_answer(model, config, purpose=purpose, system=system, user=user)

    return AgentDependencies(
        load_schema=load_schema,
        draft_sql=draft,
        write_answer=answer,
        query_tool=ReadOnlyQueryTool(run=client.execute),
        query_timeout_seconds=settings.database_query_timeout_seconds,
    )


def answer_question(deps: AgentDependencies, request: AgentInput) -> AgentOutput:
    """Compile the graph, run one question, and return the caller-facing result."""
    compiled = compile_graph(production_nodes(deps))
    final: dict[str, Any] = compiled.invoke(
        initial_state(request),
        config={"recursion_limit": RECURSION_LIMIT},
    )
    return to_output(cast(AgentState, final))


def _record(state: AgentState, metadata: ModelCallMetadata) -> tuple[ModelCallMetadata, ...]:
    """Append one model call to the run's history."""
    return state["model_calls"] + (metadata,)


def _generation_failed(reason: str | None) -> dict[str, Any]:
    """Record a drafting failure and spend the repair budget."""
    return {
        "disposition": None,
        "sql": None,
        "rationale": None,
        "clarification_question": None,
        "repair_attempts": MAX_REPAIR_ATTEMPTS,
        "failure_stage": FailureStage.GENERATION,
        "failure_reason": reason or "The model did not return a usable response.",
    }


def _no_statement_to_run() -> dict[str, Any]:
    """Record that execution was reached without a statement.

    Unreachable through the compiled graph, because validation rejects a missing
    statement first. Kept because a node must not assume the correctness of the
    edges that lead to it.
    """
    reason = "There is no statement to run."
    return {
        "result": None,
        "query_status": QueryStatus.FAILED,
        "execution_error": reason,
        "repair_attempts": MAX_REPAIR_ATTEMPTS,
        "failure_stage": FailureStage.EXECUTION,
        "failure_reason": reason,
    }


def _answer_failed(reason: str) -> dict[str, Any]:
    """Record a failure to describe a result that was retrieved successfully."""
    return {"outcome": AgentOutcome.FAILED, "failure_stage": FailureStage.ANSWER, "failure_reason": reason}
