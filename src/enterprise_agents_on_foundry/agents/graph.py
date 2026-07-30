"""Graph topology, routing rules, and temporary placeholder nodes.

This module contains wiring and nothing else. Every node is supplied by the
caller as a :class:`NodeSet`, so the shape of the graph can be read here in one
screen and the behaviour of a node is somewhere else entirely.

The legacy agent bound tools to the model and let the model decide, on every
turn, whether to call one. That gave it an unbounded loop and no way to say what
the model was not permitted to do. Here the model has no tools. It is called by
two nodes, both of which only ask it for a statement, and the graph decides
everything else.

The placeholder nodes in this module are temporary. They exist so that topology
and routing are testable before any model or database work is written, and they
are removed when the real nodes land.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, START, StateGraph

from enterprise_agents_on_foundry.agents.state import (
    MAX_REPAIR_ATTEMPTS,
    AgentOutcome,
    AgentState,
    FailureStage,
    GenerationDisposition,
    NodeName,
)
from enterprise_agents_on_foundry.database.tool import QueryStatus

NodeFn = Callable[[AgentState], dict[str, Any]]
"""A node takes the whole state and returns only the keys it changed."""

__all__ = [
    "NodeFn",
    "NodeSet",
    "build_graph",
    "compile_graph",
    "placeholder_nodes",
    "route_after_execution",
    "route_after_generation",
    "route_after_repair",
    "route_after_validation",
]


@dataclass(frozen=True, slots=True)
class NodeSet:
    """The seven node implementations the graph is built from.

    Passing these in rather than importing them keeps this module free of any
    dependency on a model client or a database connection, which is why the
    topology tests need neither.
    """

    load_schema: NodeFn
    generate_sql: NodeFn
    validate_sql: NodeFn
    execute_sql: NodeFn
    repair_sql: NodeFn
    compose_answer: NodeFn
    finalize: NodeFn


def route_after_generation(state: AgentState) -> NodeName:
    """Decide whether there is a statement to check at all.

    Only a ready disposition reaches validation. A clarification or an
    unsupported answer is a result, not a broken statement, so sending either
    into the validator would report "not a read-only statement" for a response
    that never claimed to be one.
    """
    if state["disposition"] is GenerationDisposition.READY:
        return NodeName.VALIDATE_SQL
    return NodeName.FINALIZE


def route_after_validation(state: AgentState) -> NodeName:
    """Decide where to go once a statement has been checked.

    Validation failure is repairable exactly once. After that the graph gives
    up, because a model that produced unsafe or malformed SQL twice in a row is
    not going to be talked round by a third prompt.
    """
    if state["validation_error"] is None:
        return NodeName.EXECUTE_SQL
    if state["repair_attempts"] < MAX_REPAIR_ATTEMPTS:
        return NodeName.REPAIR_SQL
    return NodeName.FINALIZE


def route_after_execution(state: AgentState) -> NodeName:
    """Decide where to go once a statement has been run.

    A query that matched nothing ran correctly, so it is answered rather than
    repaired. Rewriting a valid statement because the data disagreed with the
    question is how an agent talks itself into inventing rows.

    A refusal or a failure is repairable under the same budget as a validation
    error. The three share one counter deliberately: the cost being bounded is
    the number of statements the model is allowed to write.
    """
    if state["query_status"] in {QueryStatus.SUCCESS, QueryStatus.EMPTY}:
        return NodeName.COMPOSE_ANSWER
    if state["repair_attempts"] < MAX_REPAIR_ATTEMPTS:
        return NodeName.REPAIR_SQL
    return NodeName.FINALIZE


def route_after_repair(state: AgentState) -> NodeName:
    """Send a repaired statement back through validation.

    Never to execution. A repaired statement is model output and is therefore
    exactly as untrusted as the statement it replaced.

    A repair may also conclude that the question needs clarification or cannot
    be answered at all, which ends the run rather than producing a statement.
    """
    if state["disposition"] is GenerationDisposition.READY and state["sql"] is not None:
        return NodeName.VALIDATE_SQL
    return NodeName.FINALIZE


def build_graph(nodes: NodeSet) -> StateGraph[AgentState, None, AgentState, AgentState]:
    """Wire the seven nodes into the approved topology."""
    graph: StateGraph[AgentState, None, AgentState, AgentState] = StateGraph(AgentState)

    # LangGraph bounds its node input on a structural protocol requiring
    # ``__required_keys__``. mypy does not accept a TypedDict as satisfying that
    # protocol, so registration is the one call it cannot verify. The names and
    # the callables are still checked: ``NodeName`` is an enumeration and
    # ``NodeSet`` is typed, so a wrong name or a wrong signature still fails here.
    registrations: tuple[tuple[NodeName, NodeFn], ...] = (
        (NodeName.LOAD_SCHEMA, nodes.load_schema),
        (NodeName.GENERATE_SQL, nodes.generate_sql),
        (NodeName.VALIDATE_SQL, nodes.validate_sql),
        (NodeName.EXECUTE_SQL, nodes.execute_sql),
        (NodeName.REPAIR_SQL, nodes.repair_sql),
        (NodeName.COMPOSE_ANSWER, nodes.compose_answer),
        (NodeName.FINALIZE, nodes.finalize),
    )
    for name, node in registrations:
        graph.add_node(name.value, node)  # type: ignore[call-overload]

    graph.add_edge(START, NodeName.LOAD_SCHEMA)
    graph.add_edge(NodeName.LOAD_SCHEMA, NodeName.GENERATE_SQL)

    graph.add_conditional_edges(
        NodeName.GENERATE_SQL,
        route_after_generation,
        {
            NodeName.VALIDATE_SQL: NodeName.VALIDATE_SQL,
            NodeName.FINALIZE: NodeName.FINALIZE,
        },
    )
    graph.add_conditional_edges(
        NodeName.VALIDATE_SQL,
        route_after_validation,
        {
            NodeName.EXECUTE_SQL: NodeName.EXECUTE_SQL,
            NodeName.REPAIR_SQL: NodeName.REPAIR_SQL,
            NodeName.FINALIZE: NodeName.FINALIZE,
        },
    )
    graph.add_conditional_edges(
        NodeName.EXECUTE_SQL,
        route_after_execution,
        {
            NodeName.COMPOSE_ANSWER: NodeName.COMPOSE_ANSWER,
            NodeName.REPAIR_SQL: NodeName.REPAIR_SQL,
            NodeName.FINALIZE: NodeName.FINALIZE,
        },
    )
    graph.add_conditional_edges(
        NodeName.REPAIR_SQL,
        route_after_repair,
        {
            NodeName.VALIDATE_SQL: NodeName.VALIDATE_SQL,
            NodeName.FINALIZE: NodeName.FINALIZE,
        },
    )

    graph.add_edge(NodeName.COMPOSE_ANSWER, END)
    graph.add_edge(NodeName.FINALIZE, END)

    return graph


def compile_graph(nodes: NodeSet) -> Any:
    """Compile the graph so it can be invoked.

    No checkpointer is passed. Durable execution is a later release, and adding
    one now would mean designing a thread identity model before anything needs
    one.
    """
    return build_graph(nodes).compile()


def placeholder_nodes(
    *,
    disposition: GenerationDisposition = GenerationDisposition.READY,
    validation_error: str | None = None,
    query_status: QueryStatus = QueryStatus.SUCCESS,
    repair_produces_sql: bool = True,
    repair_fixes: bool = True,
) -> NodeSet:
    """Build deterministic stand-in nodes for topology and routing tests.

    Temporary. These call no model and no database, and each argument selects
    one branch of the graph, so a test states the scenario it wants rather than
    stubbing seven functions.
    """

    def load_schema(state: AgentState) -> dict[str, Any]:
        return {"schema_context": "SalesLT (placeholder schema)"}

    def generate_sql(state: AgentState) -> dict[str, Any]:
        if disposition is GenerationDisposition.READY:
            return {"disposition": disposition, "sql": "SELECT TOP 1 1 AS placeholder", "rationale": "placeholder"}
        return {
            "disposition": disposition,
            "sql": None,
            "rationale": "placeholder",
            "clarification_question": "Which year?"
            if disposition is GenerationDisposition.CLARIFICATION_REQUIRED
            else None,
        }

    def validate_sql(state: AgentState) -> dict[str, Any]:
        if state["repair_attempts"] > 0 and repair_fixes:
            return {"validation_error": None}
        return {"validation_error": validation_error}

    def execute_sql(state: AgentState) -> dict[str, Any]:
        if state["repair_attempts"] > 0 and repair_fixes:
            return {"query_status": QueryStatus.SUCCESS, "execution_error": None}
        error = (
            None if query_status in {QueryStatus.SUCCESS, QueryStatus.EMPTY} else f"placeholder {query_status.value}"
        )
        return {"query_status": query_status, "execution_error": error}

    def repair_sql(state: AgentState) -> dict[str, Any]:
        if not repair_produces_sql:
            return {
                "disposition": None,
                "sql": None,
                "repair_attempts": state["repair_attempts"] + 1,
                "validation_error": None,
                "execution_error": None,
                "query_status": None,
            }
        return {
            "disposition": GenerationDisposition.READY,
            "sql": "SELECT TOP 1 2 AS repaired",
            "repair_attempts": state["repair_attempts"] + 1,
        }

    def compose_answer(state: AgentState) -> dict[str, Any]:
        outcome = AgentOutcome.EMPTY if state["query_status"] is QueryStatus.EMPTY else AgentOutcome.SUCCEEDED
        return {"answer": "placeholder answer", "outcome": outcome}

    def finalize(state: AgentState) -> dict[str, Any]:
        if state["disposition"] is GenerationDisposition.CLARIFICATION_REQUIRED:
            return {"outcome": AgentOutcome.CLARIFICATION_REQUIRED}
        if state["disposition"] is GenerationDisposition.UNSUPPORTED:
            return {"outcome": AgentOutcome.UNSUPPORTED}

        rejected = state["validation_error"] is not None or state["query_status"] is QueryStatus.REJECTED
        stage = FailureStage.VALIDATION if state["validation_error"] else FailureStage.EXECUTION
        if state["sql"] is None:
            stage = FailureStage.GENERATION
        return {
            "outcome": AgentOutcome.REJECTED if rejected else AgentOutcome.FAILED,
            "failure_stage": stage,
            "failure_reason": state["validation_error"] or state["execution_error"] or "no statement produced",
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
