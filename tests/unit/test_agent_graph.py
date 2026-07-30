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
    route_after_generation,
    route_after_repair,
    route_after_validation,
)
from enterprise_agents_on_foundry.agents.state import (
    MAX_REPAIR_ATTEMPTS,
    AgentInput,
    AgentOutcome,
    FailureStage,
    GenerationDisposition,
    NodeName,
    SqlGenerationResult,
    initial_state,
)
from enterprise_agents_on_foundry.database.tool import QueryStatus


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
            "disposition",
            "sql",
            "rationale",
            "clarification_question",
            "validation_error",
            "query_status",
            "execution_error",
            "repair_attempts",
            "result",
            "answer",
            "outcome",
            "failure_stage",
            "failure_reason",
            "model_calls",
        }

    def test_initial_state_starts_with_no_repairs_spent(self) -> None:
        assert initial_state(AgentInput(question="anything"))["repair_attempts"] == 0

    def test_initial_state_starts_with_no_model_calls_recorded(self) -> None:
        assert initial_state(AgentInput(question="anything"))["model_calls"] == ()

    def test_blank_question_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="question"):
            AgentInput(question="   ")

    def test_row_cap_is_bounded(self) -> None:
        with pytest.raises(ValueError, match="max_rows"):
            AgentInput(question="anything", max_rows=10_001)

    def test_a_ready_generation_carries_a_stripped_statement(self) -> None:
        generation = SqlGenerationResult(disposition=GenerationDisposition.READY, sql="  SELECT TOP 1 1  ")
        assert generation.sql == "SELECT TOP 1 1"
        assert generation.clarification_question is None

    def test_a_ready_generation_without_a_statement_is_rejected(self) -> None:
        """Ready means executable. A blank statement is not a decision the graph can act on."""
        with pytest.raises(ValueError, match="sql is required"):
            SqlGenerationResult(disposition=GenerationDisposition.READY, sql="   ")

    def test_clarification_requires_a_question_to_ask(self) -> None:
        with pytest.raises(ValueError, match="clarification_question is required"):
            SqlGenerationResult(disposition=GenerationDisposition.CLARIFICATION_REQUIRED)

    def test_clarification_may_not_smuggle_a_statement(self) -> None:
        with pytest.raises(ValueError, match="sql must be absent"):
            SqlGenerationResult(
                disposition=GenerationDisposition.CLARIFICATION_REQUIRED,
                sql="SELECT TOP 1 1",
                clarification_question="Which year did you mean?",
            )

    def test_an_unsupported_question_may_not_carry_a_statement(self) -> None:
        """The contract is what stops a refusal from arriving with SQL attached."""
        with pytest.raises(ValueError, match="sql must be absent"):
            SqlGenerationResult(
                disposition=GenerationDisposition.UNSUPPORTED,
                sql="DELETE FROM SalesLT.Product",
            )

    def test_generation_refuses_unexpected_fields(self) -> None:
        """The model may return a decision and its supporting fields, and nothing else."""
        with pytest.raises(ValueError, match="tool_calls"):
            SqlGenerationResult(
                disposition=GenerationDisposition.READY,
                sql="SELECT TOP 1 1",
                tool_calls=[{"name": "run_query"}],
            )

    def test_only_one_repair_is_budgeted(self) -> None:
        assert MAX_REPAIR_ATTEMPTS == 1


class TestRouting:
    """Every conditional edge, as a pure function."""

    def test_a_ready_statement_goes_to_validation(self) -> None:
        state = _state(disposition=GenerationDisposition.READY, sql="SELECT TOP 1 1")
        assert route_after_generation(state) is NodeName.VALIDATE_SQL  # type: ignore[arg-type]

    def test_clarification_never_reaches_validation(self) -> None:
        state = _state(
            disposition=GenerationDisposition.CLARIFICATION_REQUIRED,
            clarification_question="Which year did you mean?",
        )
        assert route_after_generation(state) is NodeName.FINALIZE  # type: ignore[arg-type]

    def test_an_unsupported_question_never_reaches_validation(self) -> None:
        state = _state(disposition=GenerationDisposition.UNSUPPORTED)
        assert route_after_generation(state) is NodeName.FINALIZE  # type: ignore[arg-type]

    def test_a_generation_failure_ends_the_run(self) -> None:
        assert route_after_generation(_state(disposition=None)) is NodeName.FINALIZE  # type: ignore[arg-type]

    def test_valid_sql_goes_to_execution(self) -> None:
        assert route_after_validation(_state()) is NodeName.EXECUTE_SQL  # type: ignore[arg-type]

    def test_first_validation_failure_goes_to_repair(self) -> None:
        state = _state(validation_error="not a read-only statement", repair_attempts=0)
        assert route_after_validation(state) is NodeName.REPAIR_SQL  # type: ignore[arg-type]

    def test_second_validation_failure_gives_up(self) -> None:
        state = _state(validation_error="not a read-only statement", repair_attempts=1)
        assert route_after_validation(state) is NodeName.FINALIZE  # type: ignore[arg-type]

    def test_successful_execution_goes_to_the_answer(self) -> None:
        state = _state(query_status=QueryStatus.SUCCESS)
        assert route_after_execution(state) is NodeName.COMPOSE_ANSWER  # type: ignore[arg-type]

    def test_an_empty_result_is_still_answered(self) -> None:
        """No matching rows is a true answer, not a failure to repair."""
        state = _state(query_status=QueryStatus.EMPTY)
        assert route_after_execution(state) is NodeName.COMPOSE_ANSWER  # type: ignore[arg-type]

    def test_a_rejected_query_goes_to_repair(self) -> None:
        state = _state(query_status=QueryStatus.REJECTED, execution_error="not a read-only statement")
        assert route_after_execution(state) is NodeName.REPAIR_SQL  # type: ignore[arg-type]

    def test_first_execution_failure_goes_to_repair(self) -> None:
        state = _state(query_status=QueryStatus.FAILED, execution_error="Invalid column name", repair_attempts=0)
        assert route_after_execution(state) is NodeName.REPAIR_SQL  # type: ignore[arg-type]

    def test_second_execution_failure_gives_up(self) -> None:
        state = _state(query_status=QueryStatus.FAILED, execution_error="Invalid column name", repair_attempts=1)
        assert route_after_execution(state) is NodeName.FINALIZE  # type: ignore[arg-type]

    def test_repair_always_returns_to_validation(self) -> None:
        """A repaired statement is model output, so it is revalidated, never executed directly."""
        state = _state(disposition=GenerationDisposition.READY, sql="SELECT TOP 1 2", repair_attempts=1)
        assert route_after_repair(state) is NodeName.VALIDATE_SQL  # type: ignore[arg-type]

    def test_repair_without_a_statement_gives_up(self) -> None:
        state = _state(disposition=None, sql=None, repair_attempts=1)
        assert route_after_repair(state) is NodeName.FINALIZE  # type: ignore[arg-type]

    def test_a_repair_that_concludes_unsupported_gives_up(self) -> None:
        state = _state(disposition=GenerationDisposition.UNSUPPORTED, sql=None, repair_attempts=1)
        assert route_after_repair(state) is NodeName.FINALIZE  # type: ignore[arg-type]

    def test_no_route_reaches_execution_without_passing_validation(self) -> None:
        state = _state(disposition=GenerationDisposition.READY, sql="SELECT TOP 1 2")
        assert route_after_repair(state) is not NodeName.EXECUTE_SQL  # type: ignore[arg-type]
        assert route_after_generation(state) is not NodeName.EXECUTE_SQL  # type: ignore[arg-type]


class TestTopology:
    """The compiled graph matches the approved diagram."""

    def test_every_declared_node_is_present(self) -> None:
        graph = build_graph(placeholder_nodes()).compile().get_graph()
        assert {node for node in graph.nodes if node not in {START, END}} == {name.value for name in NodeName}

    def test_the_graph_starts_by_loading_the_schema(self) -> None:
        graph = build_graph(placeholder_nodes()).compile().get_graph()
        assert {edge.target for edge in graph.edges if edge.source == START} == {NodeName.LOAD_SCHEMA.value}

    def test_only_the_answer_and_terminal_nodes_end_the_run(self) -> None:
        graph = build_graph(placeholder_nodes()).compile().get_graph()
        sources = {edge.source for edge in graph.edges if edge.target == END}
        assert sources == {NodeName.COMPOSE_ANSWER.value, NodeName.FINALIZE.value}

    def test_generation_leads_only_to_validation_or_the_terminal_node(self) -> None:
        """Clarification and unsupported questions have nothing to validate."""
        graph = build_graph(placeholder_nodes()).compile().get_graph()
        targets = {edge.target for edge in graph.edges if edge.source == NodeName.GENERATE_SQL.value}
        assert targets == {NodeName.VALIDATE_SQL.value, NodeName.FINALIZE.value}

    def test_generation_is_never_reachable_from_repair(self) -> None:
        """Repair replaces a statement. It does not restart the question."""
        graph = build_graph(placeholder_nodes()).compile().get_graph()
        targets = {edge.target for edge in graph.edges if edge.source == NodeName.REPAIR_SQL.value}
        assert targets == {NodeName.VALIDATE_SQL.value, NodeName.FINALIZE.value}

    def test_the_graph_compiles(self) -> None:
        assert compile_graph(placeholder_nodes()) is not None


class TestBoundedExecution:
    """End-to-end runs over the placeholder nodes, with no model and no database."""

    def test_a_clean_question_answers_without_repairing(self) -> None:
        final = compile_graph(placeholder_nodes()).invoke(initial_state(AgentInput(question="how many products")))
        assert final["outcome"] is AgentOutcome.SUCCEEDED
        assert final["repair_attempts"] == 0

    def test_an_ambiguous_question_ends_with_a_clarification(self) -> None:
        nodes = placeholder_nodes(disposition=GenerationDisposition.CLARIFICATION_REQUIRED)
        final = compile_graph(nodes).invoke(initial_state(AgentInput(question="how many sales last year")))
        assert final["outcome"] is AgentOutcome.CLARIFICATION_REQUIRED
        assert final["clarification_question"] is not None
        assert final["sql"] is None
        assert final["query_status"] is None

    def test_an_unsupported_question_ends_without_running_anything(self) -> None:
        nodes = placeholder_nodes(disposition=GenerationDisposition.UNSUPPORTED)
        final = compile_graph(nodes).invoke(initial_state(AgentInput(question="what is the weather")))
        assert final["outcome"] is AgentOutcome.UNSUPPORTED
        assert final["sql"] is None
        assert final["query_status"] is None

    def test_an_empty_result_is_reported_as_its_own_outcome(self) -> None:
        nodes = placeholder_nodes(query_status=QueryStatus.EMPTY)
        final = compile_graph(nodes).invoke(initial_state(AgentInput(question="products costing a billion")))
        assert final["outcome"] is AgentOutcome.EMPTY
        assert final["answer"] is not None

    def test_one_validation_failure_is_repaired_and_then_answered(self) -> None:
        nodes = placeholder_nodes(validation_error="not a read-only statement")
        final = compile_graph(nodes).invoke(initial_state(AgentInput(question="delete everything")))
        assert final["outcome"] is AgentOutcome.SUCCEEDED
        assert final["repair_attempts"] == 1

    def test_a_persistently_unsafe_statement_terminates(self) -> None:
        nodes = placeholder_nodes(validation_error="not a read-only statement", repair_fixes=False)
        final = compile_graph(nodes).invoke(initial_state(AgentInput(question="delete everything")))
        assert final["outcome"] is AgentOutcome.REJECTED
        assert final["failure_stage"] is FailureStage.VALIDATION
        assert final["repair_attempts"] == MAX_REPAIR_ATTEMPTS

    def test_a_persistently_rejected_query_terminates(self) -> None:
        nodes = placeholder_nodes(query_status=QueryStatus.REJECTED, repair_fixes=False)
        final = compile_graph(nodes).invoke(initial_state(AgentInput(question="drop the products table")))
        assert final["outcome"] is AgentOutcome.REJECTED

    def test_a_persistent_execution_error_terminates(self) -> None:
        nodes = placeholder_nodes(query_status=QueryStatus.FAILED, repair_fixes=False)
        final = compile_graph(nodes).invoke(initial_state(AgentInput(question="how many widgets")))
        assert final["outcome"] is AgentOutcome.FAILED
        assert final["failure_stage"] is FailureStage.EXECUTION

    def test_a_repair_that_produces_nothing_terminates(self) -> None:
        nodes = placeholder_nodes(
            validation_error="not a read-only statement",
            repair_produces_sql=False,
            repair_fixes=False,
        )
        final = compile_graph(nodes).invoke(initial_state(AgentInput(question="delete everything")))
        assert final["outcome"] is AgentOutcome.FAILED
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
        assert visited[-1] == NodeName.FINALIZE.value
