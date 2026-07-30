"""Topology and routing tests for the Text-to-SQL graph.

These need no model, no database, and no Azure environment. That is the point of
keeping state explicit: the entire control flow is decidable from a dictionary.
"""

from __future__ import annotations

import pytest
from langgraph.graph import END, START

from enterprise_agents_on_foundry.agents.graph import (
    build_graph,
    compile_graph,
    placeholder_nodes,
    route_after_execution,
    route_after_repair,
    route_after_validation,
)
from enterprise_agents_on_foundry.agents.state import (
    MAX_REPAIR_ATTEMPTS,
    AgentInput,
    AgentStatus,
    FailureStage,
    NodeName,
    SqlDraft,
    initial_state,
)


def _state(**overrides: object) -> dict[str, object]:
    """Build a state dictionary for a routing test."""
    state = dict(initial_state(AgentInput(question="how many products are there")))
    state.update(overrides)
    return state


class TestState:
    """The typed contracts the graph is built on."""

    def test_initial_state_populates_every_key(self) -> None:
        state = initial_state(AgentInput(question="how many products are there"))
        assert set(state) == {
            "question",
            "max_rows",
            "schema_context",
            "sql",
            "rationale",
            "validation_error",
            "execution_error",
            "repair_attempts",
            "result",
            "answer",
            "status",
            "failure_stage",
            "failure_reason",
        }

    def test_initial_state_starts_with_no_repairs_spent(self) -> None:
        assert initial_state(AgentInput(question="anything"))["repair_attempts"] == 0

    def test_blank_question_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="question"):
            AgentInput(question="   ")

    def test_row_cap_is_bounded(self) -> None:
        with pytest.raises(ValueError, match="max_rows"):
            AgentInput(question="anything", max_rows=10_001)

    def test_sql_draft_strips_and_rejects_blank(self) -> None:
        assert SqlDraft(sql="  SELECT TOP 1 1  ").sql == "SELECT TOP 1 1"
        with pytest.raises(ValueError, match="sql"):
            SqlDraft(sql="   ")

    def test_sql_draft_refuses_unexpected_fields(self) -> None:
        """The model may return a statement and a reason, and nothing else."""
        with pytest.raises(ValueError, match="tool_calls"):
            SqlDraft(sql="SELECT TOP 1 1", tool_calls=[{"name": "run_query"}])

    def test_only_one_repair_is_budgeted(self) -> None:
        assert MAX_REPAIR_ATTEMPTS == 1


class TestRouting:
    """Every conditional edge, as a pure function."""

    def test_valid_sql_goes_to_execution(self) -> None:
        assert route_after_validation(_state()) is NodeName.EXECUTE_SQL  # type: ignore[arg-type]

    def test_first_validation_failure_goes_to_repair(self) -> None:
        state = _state(validation_error="not a read-only statement", repair_attempts=0)
        assert route_after_validation(state) is NodeName.REPAIR_SQL  # type: ignore[arg-type]

    def test_second_validation_failure_gives_up(self) -> None:
        state = _state(validation_error="not a read-only statement", repair_attempts=1)
        assert route_after_validation(state) is NodeName.FAIL  # type: ignore[arg-type]

    def test_successful_execution_goes_to_the_answer(self) -> None:
        assert route_after_execution(_state()) is NodeName.COMPOSE_ANSWER  # type: ignore[arg-type]

    def test_first_execution_failure_goes_to_repair(self) -> None:
        state = _state(execution_error="Invalid column name", repair_attempts=0)
        assert route_after_execution(state) is NodeName.REPAIR_SQL  # type: ignore[arg-type]

    def test_second_execution_failure_gives_up(self) -> None:
        state = _state(execution_error="Invalid column name", repair_attempts=1)
        assert route_after_execution(state) is NodeName.FAIL  # type: ignore[arg-type]

    def test_repair_always_returns_to_validation(self) -> None:
        """A repaired statement is model output, so it is revalidated, never executed directly."""
        state = _state(sql="SELECT TOP 1 2", repair_attempts=1)
        assert route_after_repair(state) is NodeName.VALIDATE_SQL  # type: ignore[arg-type]

    def test_repair_without_a_statement_gives_up(self) -> None:
        state = _state(sql=None, repair_attempts=1)
        assert route_after_repair(state) is NodeName.FAIL  # type: ignore[arg-type]

    def test_no_route_reaches_execution_without_passing_validation(self) -> None:
        assert route_after_repair(_state(sql="SELECT TOP 1 2")) is not NodeName.EXECUTE_SQL  # type: ignore[arg-type]


class TestTopology:
    """The compiled graph matches the approved diagram."""

    def test_every_declared_node_is_present(self) -> None:
        graph = build_graph(placeholder_nodes()).compile().get_graph()
        assert {node for node in graph.nodes if node not in {START, END}} == {name.value for name in NodeName}

    def test_the_graph_starts_by_loading_the_schema(self) -> None:
        graph = build_graph(placeholder_nodes()).compile().get_graph()
        assert {edge.target for edge in graph.edges if edge.source == START} == {NodeName.LOAD_SCHEMA.value}

    def test_only_the_answer_and_failure_nodes_terminate(self) -> None:
        graph = build_graph(placeholder_nodes()).compile().get_graph()
        sources = {edge.source for edge in graph.edges if edge.target == END}
        assert sources == {NodeName.COMPOSE_ANSWER.value, NodeName.FAIL.value}

    def test_generation_is_never_reachable_from_repair(self) -> None:
        """Repair replaces a statement. It does not restart the question."""
        graph = build_graph(placeholder_nodes()).compile().get_graph()
        targets = {edge.target for edge in graph.edges if edge.source == NodeName.REPAIR_SQL.value}
        assert targets == {NodeName.VALIDATE_SQL.value, NodeName.FAIL.value}

    def test_the_graph_compiles(self) -> None:
        assert compile_graph(placeholder_nodes()) is not None


class TestBoundedExecution:
    """End-to-end runs over the placeholder nodes, with no model and no database."""

    def test_a_clean_question_answers_without_repairing(self) -> None:
        final = compile_graph(placeholder_nodes()).invoke(initial_state(AgentInput(question="how many products")))
        assert final["status"] is AgentStatus.SUCCEEDED
        assert final["repair_attempts"] == 0

    def test_one_validation_failure_is_repaired_and_then_answered(self) -> None:
        nodes = placeholder_nodes(validation_error="not a read-only statement")
        final = compile_graph(nodes).invoke(initial_state(AgentInput(question="delete everything")))
        assert final["status"] is AgentStatus.SUCCEEDED
        assert final["repair_attempts"] == 1

    def test_a_persistently_unsafe_statement_terminates(self) -> None:
        nodes = placeholder_nodes(validation_error="not a read-only statement", repair_fixes=False)
        final = compile_graph(nodes).invoke(initial_state(AgentInput(question="delete everything")))
        assert final["status"] is AgentStatus.FAILED
        assert final["failure_stage"] is FailureStage.VALIDATION
        assert final["repair_attempts"] == MAX_REPAIR_ATTEMPTS

    def test_a_persistent_execution_error_terminates(self) -> None:
        nodes = placeholder_nodes(execution_error="Invalid column name", repair_fixes=False)
        final = compile_graph(nodes).invoke(initial_state(AgentInput(question="how many widgets")))
        assert final["status"] is AgentStatus.FAILED
        assert final["failure_stage"] is FailureStage.EXECUTION

    def test_a_repair_that_produces_nothing_terminates(self) -> None:
        nodes = placeholder_nodes(
            validation_error="not a read-only statement",
            repair_produces_sql=False,
            repair_fixes=False,
        )
        final = compile_graph(nodes).invoke(initial_state(AgentInput(question="delete everything")))
        assert final["status"] is AgentStatus.FAILED
        assert final["failure_stage"] is FailureStage.GENERATION

    def test_a_failing_run_visits_repair_exactly_once(self) -> None:
        """The bound is the attempt counter, not the recursion limit."""
        nodes = placeholder_nodes(validation_error="not a read-only statement", repair_fixes=False)
        visited = [
            step
            for chunk in compile_graph(nodes).stream(
                initial_state(AgentInput(question="delete everything")),
                stream_mode="updates",
            )
            for step in chunk
        ]
        assert visited.count(NodeName.REPAIR_SQL.value) == 1
        assert visited[-1] == NodeName.FAIL.value
