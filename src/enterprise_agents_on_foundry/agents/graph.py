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
    AgentState,
    AgentStatus,
    FailureStage,
    NodeName,
)

NodeFn = Callable[[AgentState], dict[str, Any]]
"""A node takes the whole state and returns only the keys it changed."""

__all__ = [
    "NodeFn",
    "NodeSet",
    "build_graph",
    "compile_graph",
    "placeholder_nodes",
    "route_after_execution",
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
    fail: NodeFn


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
    return NodeName.FAIL


def route_after_execution(state: AgentState) -> NodeName:
    """Decide where to go once a statement has been run.

    An execution error is repairable under the same budget as a validation
    error. The two share one counter deliberately: the cost being bounded is the
    number of statements the model is allowed to write, not the number of times
    each individual check may fail.
    """
    if state["execution_error"] is None:
        return NodeName.COMPOSE_ANSWER
    if state["repair_attempts"] < MAX_REPAIR_ATTEMPTS:
        return NodeName.REPAIR_SQL
    return NodeName.FAIL


def route_after_repair(state: AgentState) -> NodeName:
    """Send a repaired statement back through validation.

    Never to execution. A repaired statement is model output and is therefore
    exactly as untrusted as the statement it replaced.
    """
    if state["sql"] is None:
        return NodeName.FAIL
    return NodeName.VALIDATE_SQL


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
        (NodeName.FAIL, nodes.fail),
    )
    for name, node in registrations:
        graph.add_node(name.value, node)  # type: ignore[call-overload]

    graph.add_edge(START, NodeName.LOAD_SCHEMA)
    graph.add_edge(NodeName.LOAD_SCHEMA, NodeName.GENERATE_SQL)
    graph.add_edge(NodeName.GENERATE_SQL, NodeName.VALIDATE_SQL)

    graph.add_conditional_edges(
        NodeName.VALIDATE_SQL,
        route_after_validation,
        {
            NodeName.EXECUTE_SQL: NodeName.EXECUTE_SQL,
            NodeName.REPAIR_SQL: NodeName.REPAIR_SQL,
            NodeName.FAIL: NodeName.FAIL,
        },
    )
    graph.add_conditional_edges(
        NodeName.EXECUTE_SQL,
        route_after_execution,
        {
            NodeName.COMPOSE_ANSWER: NodeName.COMPOSE_ANSWER,
            NodeName.REPAIR_SQL: NodeName.REPAIR_SQL,
            NodeName.FAIL: NodeName.FAIL,
        },
    )
    graph.add_conditional_edges(
        NodeName.REPAIR_SQL,
        route_after_repair,
        {
            NodeName.VALIDATE_SQL: NodeName.VALIDATE_SQL,
            NodeName.FAIL: NodeName.FAIL,
        },
    )

    graph.add_edge(NodeName.COMPOSE_ANSWER, END)
    graph.add_edge(NodeName.FAIL, END)

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
    validation_error: str | None = None,
    execution_error: str | None = None,
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
        return {"sql": "SELECT TOP 1 1 AS placeholder", "rationale": "placeholder"}

    def validate_sql(state: AgentState) -> dict[str, Any]:
        if state["repair_attempts"] > 0 and repair_fixes:
            return {"validation_error": None}
        return {"validation_error": validation_error}

    def execute_sql(state: AgentState) -> dict[str, Any]:
        if state["repair_attempts"] > 0 and repair_fixes:
            return {"execution_error": None}
        return {"execution_error": execution_error}

    def repair_sql(state: AgentState) -> dict[str, Any]:
        return {
            "sql": "SELECT TOP 1 2 AS repaired" if repair_produces_sql else None,
            "repair_attempts": state["repair_attempts"] + 1,
        }

    def compose_answer(state: AgentState) -> dict[str, Any]:
        return {"answer": "placeholder answer", "status": AgentStatus.SUCCEEDED}

    def fail(state: AgentState) -> dict[str, Any]:
        stage = FailureStage.VALIDATION if state["validation_error"] else FailureStage.EXECUTION
        if state["sql"] is None:
            stage = FailureStage.GENERATION
        return {
            "status": AgentStatus.FAILED,
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
        fail=fail,
    )
