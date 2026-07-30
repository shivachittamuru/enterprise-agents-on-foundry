"""Behaviour of the production nodes, exercised without Azure.

Every dependency is a plain function, so these tests state what the model
returned and what the database did, then assert what the graph decided. The
legacy agent had no equivalent: its behaviour could only be observed by calling
a live model against a live database.
"""

from __future__ import annotations

import pytest

from enterprise_agents_on_foundry.agents.graph import compile_graph
from enterprise_agents_on_foundry.agents.nodes import (
    QUERY_LABEL,
    RECURSION_LIMIT,
    AgentDependencies,
    SqlText,
    answer_question,
    production_nodes,
)
from enterprise_agents_on_foundry.agents.prompts import (
    ANSWER_SYSTEM_PROMPT,
    REPAIR_SYSTEM_PROMPT,
    SQL_SYSTEM_PROMPT,
)
from enterprise_agents_on_foundry.agents.schema_context import render_schema_context
from enterprise_agents_on_foundry.agents.state import (
    AgentInput,
    AgentStatus,
    FailureStage,
    initial_state,
)
from enterprise_agents_on_foundry.database.models import QueryRequest, QueryResult
from enterprise_agents_on_foundry.errors import (
    DatabaseConnectionError,
    ModelOutputError,
    QueryValidationError,
)

GOOD_SQL = "SELECT TOP (10) Name FROM SalesLT.Product"
UNSAFE_SQL = "DELETE FROM SalesLT.Product"
SCHEMA = "SalesLT.Product\n  Name nvarchar not null"


class FakeModel:
    """Records prompts and replays scripted responses."""

    def __init__(self, drafts: list[SqlText | ModelOutputError], answer: str | ModelOutputError = "An answer.") -> None:
        self._drafts = list(drafts)
        self._answer = answer
        self.draft_systems: list[str] = []
        self.draft_users: list[str] = []
        self.answer_systems: list[str] = []

    def draft(self, system: str, user: str) -> SqlText:
        self.draft_systems.append(system)
        self.draft_users.append(user)
        if not self._drafts:
            raise AssertionError("the model was asked for more drafts than the test scripted")
        response = self._drafts.pop(0)
        if isinstance(response, ModelOutputError):
            raise response
        return response

    def write(self, system: str, user: str) -> str:
        self.answer_systems.append(system)
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


class FakeDatabase:
    """Records requests and replays scripted outcomes."""

    def __init__(self, outcomes: list[QueryResult | Exception] | None = None) -> None:
        self._outcomes = list(outcomes or [_result()])
        self.requests: list[QueryRequest] = []

    def run(self, request: QueryRequest) -> QueryResult:
        self.requests.append(request)
        if not self._outcomes:
            raise AssertionError("the database was called more times than the test scripted")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _result(rows: tuple[tuple[object, ...], ...] = (("Bike",),), *, truncated: bool = False) -> QueryResult:
    return QueryResult(
        columns=("Name",),
        rows=rows,
        truncated=truncated,
        elapsed_ms=1.0,
        label=QUERY_LABEL,
    )


def _dependencies(
    *,
    model: FakeModel | None = None,
    database: FakeDatabase | None = None,
    schema: str = SCHEMA,
) -> AgentDependencies:
    fake_model = model or FakeModel([SqlText(sql=GOOD_SQL, rationale="because")])
    fake_database = database or FakeDatabase()
    return AgentDependencies(
        load_schema=lambda: schema,
        draft_sql=fake_model.draft,
        write_answer=fake_model.write,
        run_query=fake_database.run,
    )


def _state(**overrides: object) -> dict[str, object]:
    state = dict(initial_state(AgentInput(question="how many products?")))
    state.update(overrides)
    return state


class TestHappyPath:
    """One question, one statement, one answer."""

    def test_returns_a_successful_output(self) -> None:
        output = answer_question(_dependencies(), AgentInput(question="how many products?"))

        assert output.succeeded
        assert output.status is AgentStatus.SUCCEEDED
        assert output.answer == "An answer."
        assert output.sql == GOOD_SQL
        assert output.repair_attempts == 0
        assert output.failure_stage is None

    def test_reports_the_row_count_from_the_result(self) -> None:
        database = FakeDatabase([_result(rows=(("Bike",), ("Helmet",)))])
        output = answer_question(_dependencies(database=database), AgentInput(question="what products?"))

        assert output.row_count == 2

    def test_calls_the_model_once_for_sql_and_once_for_the_answer(self) -> None:
        model = FakeModel([SqlText(sql=GOOD_SQL, rationale="")])
        answer_question(_dependencies(model=model), AgentInput(question="what products?"))

        assert model.draft_systems == [SQL_SYSTEM_PROMPT]
        assert model.answer_systems == [ANSWER_SYSTEM_PROMPT]

    def test_passes_the_schema_and_question_to_the_model(self) -> None:
        model = FakeModel([SqlText(sql=GOOD_SQL, rationale="")])
        answer_question(_dependencies(model=model), AgentInput(question="which bikes are red?"))

        assert SCHEMA in model.draft_users[0]
        assert "which bikes are red?" in model.draft_users[0]


class TestExecutionBoundary:
    """What actually reaches the database."""

    def test_applies_the_requested_row_cap_to_the_request(self) -> None:
        database = FakeDatabase()
        answer_question(_dependencies(database=database), AgentInput(question="what products?", max_rows=7))

        assert database.requests[0].max_rows == 7

    def test_labels_every_agent_query(self) -> None:
        database = FakeDatabase()
        answer_question(_dependencies(database=database), AgentInput(question="what products?"))

        assert database.requests[0].label == QUERY_LABEL

    def test_never_executes_an_unsafe_statement(self) -> None:
        model = FakeModel([SqlText(sql=UNSAFE_SQL, rationale=""), SqlText(sql=UNSAFE_SQL, rationale="")])
        database = FakeDatabase()
        output = answer_question(_dependencies(model=model, database=database), AgentInput(question="delete them"))

        assert database.requests == []
        assert output.status is AgentStatus.FAILED
        assert output.failure_stage is FailureStage.VALIDATION

    def test_reports_the_forbidden_keyword_in_the_failure_reason(self) -> None:
        model = FakeModel([SqlText(sql=UNSAFE_SQL, rationale=""), SqlText(sql=UNSAFE_SQL, rationale="")])
        output = answer_question(_dependencies(model=model), AgentInput(question="delete them"))

        assert output.failure_reason is not None
        assert "DELETE" in output.failure_reason


class TestRepair:
    """The single correction attempt."""

    def test_repairs_an_invalid_statement_and_then_succeeds(self) -> None:
        model = FakeModel([SqlText(sql=UNSAFE_SQL, rationale=""), SqlText(sql=GOOD_SQL, rationale="")])
        output = answer_question(_dependencies(model=model), AgentInput(question="what products?"))

        assert output.succeeded
        assert output.repair_attempts == 1
        assert output.sql == GOOD_SQL
        assert model.draft_systems == [SQL_SYSTEM_PROMPT, REPAIR_SYSTEM_PROMPT]

    def test_gives_the_repair_prompt_the_failed_statement_and_the_error(self) -> None:
        model = FakeModel([SqlText(sql=UNSAFE_SQL, rationale=""), SqlText(sql=GOOD_SQL, rationale="")])
        answer_question(_dependencies(model=model), AgentInput(question="what products?"))

        repair_prompt = model.draft_users[1]
        assert UNSAFE_SQL in repair_prompt
        assert "DELETE" in repair_prompt

    def test_repairs_an_execution_failure(self) -> None:
        model = FakeModel([SqlText(sql=GOOD_SQL, rationale=""), SqlText(sql=GOOD_SQL, rationale="")])
        database = FakeDatabase([DatabaseConnectionError("Query failed: invalid column"), _result()])
        output = answer_question(_dependencies(model=model, database=database), AgentInput(question="what products?"))

        assert output.succeeded
        assert output.repair_attempts == 1

    def test_asks_the_model_for_at_most_two_statements(self) -> None:
        model = FakeModel([SqlText(sql=UNSAFE_SQL, rationale=""), SqlText(sql=UNSAFE_SQL, rationale="")])
        output = answer_question(_dependencies(model=model), AgentInput(question="what products?"))

        assert len(model.draft_systems) == 2
        assert output.status is AgentStatus.FAILED

    def test_a_second_execution_failure_is_terminal(self) -> None:
        model = FakeModel([SqlText(sql=GOOD_SQL, rationale=""), SqlText(sql=GOOD_SQL, rationale="")])
        database = FakeDatabase([DatabaseConnectionError("boom"), DatabaseConnectionError("boom again")])
        output = answer_question(_dependencies(model=model, database=database), AgentInput(question="what products?"))

        assert output.status is AgentStatus.FAILED
        assert output.failure_stage is FailureStage.EXECUTION
        assert output.failure_reason == "boom again"
        assert len(database.requests) == 2


class TestModelFailures:
    """Unusable model output is a reported failure, not an exception."""

    def test_a_failed_draft_ends_the_run_without_a_repair(self) -> None:
        model = FakeModel([ModelOutputError("no statement")])
        output = answer_question(_dependencies(model=model), AgentInput(question="what products?"))

        assert output.status is AgentStatus.FAILED
        assert output.failure_stage is FailureStage.GENERATION
        assert output.repair_attempts == 1
        assert len(model.draft_systems) == 1

    def test_a_failed_repair_ends_the_run(self) -> None:
        model = FakeModel([SqlText(sql=UNSAFE_SQL, rationale=""), ModelOutputError("still nothing")])
        output = answer_question(_dependencies(model=model), AgentInput(question="what products?"))

        assert output.status is AgentStatus.FAILED
        assert output.failure_reason == "still nothing"

    def test_a_failed_answer_is_reported_at_the_answer_stage(self) -> None:
        model = FakeModel([SqlText(sql=GOOD_SQL, rationale="")], answer=ModelOutputError("empty answer"))
        output = answer_question(_dependencies(model=model), AgentInput(question="what products?"))

        assert output.status is AgentStatus.FAILED
        assert output.failure_stage is FailureStage.ANSWER
        assert output.answer is None


class TestNodesInIsolation:
    """Nodes that are easier to pin down without running the whole graph."""

    def test_validate_normalises_the_statement_it_accepts(self) -> None:
        nodes = production_nodes(_dependencies())
        update = nodes.validate_sql(_state(sql=f"  {GOOD_SQL};  "))  # type: ignore[arg-type]

        assert update["sql"] == GOOD_SQL
        assert update["validation_error"] is None

    def test_execute_is_not_reached_without_a_statement(self) -> None:
        nodes = production_nodes(_dependencies())
        update = nodes.execute_sql(_state(sql=None))  # type: ignore[arg-type]

        assert update["execution_error"] is not None
        assert update["result"] is None

    def test_a_validation_error_raised_by_the_boundary_is_reported(self) -> None:
        database = FakeDatabase([QueryValidationError("Query contains 2 statements")])
        nodes = production_nodes(_dependencies(database=database))
        update = nodes.execute_sql(_state(sql=GOOD_SQL))  # type: ignore[arg-type]

        assert update["failure_stage"] is FailureStage.EXECUTION

    def test_load_schema_failures_are_not_swallowed(self) -> None:
        def broken_loader() -> str:
            raise DatabaseConnectionError("the database is unreachable")

        deps = AgentDependencies(
            load_schema=broken_loader,
            draft_sql=FakeModel([]).draft,
            write_answer=FakeModel([]).write,
            run_query=FakeDatabase().run,
        )
        with pytest.raises(DatabaseConnectionError):
            answer_question(deps, AgentInput(question="what products?"))

    def test_the_recursion_limit_exceeds_the_longest_path(self) -> None:
        assert RECURSION_LIMIT >= 9

    def test_the_graph_compiles_from_the_production_nodes(self) -> None:
        compiled = compile_graph(production_nodes(_dependencies()))

        assert set(compiled.get_graph().nodes) >= {"load_schema", "fail"}


class TestSchemaRendering:
    """The text the model is given about the database."""

    def test_groups_columns_under_their_table(self) -> None:
        rendered = render_schema_context(_schema_result())

        assert rendered.splitlines()[0] == "SalesLT.Product"
        assert "  ProductID int not null primary key" in rendered

    def test_shows_foreign_key_targets(self) -> None:
        rendered = render_schema_context(_schema_result())

        assert "-> SalesLT.ProductCategory.ProductCategoryID" in rendered

    def test_says_so_when_the_schema_is_truncated(self) -> None:
        rendered = render_schema_context(_schema_result(truncated=True))

        assert "schema truncated" in rendered


def _schema_result(*, truncated: bool = False) -> QueryResult:
    return QueryResult(
        columns=(
            "schema_name",
            "table_name",
            "column_position",
            "column_name",
            "data_type",
            "is_nullable",
            "is_primary_key",
            "references_column",
        ),
        rows=(
            ("SalesLT", "Product", 1, "ProductID", "int", False, 1, None),
            ("SalesLT", "Product", 2, "ProductCategoryID", "int", True, 0, "SalesLT.ProductCategory.ProductCategoryID"),
        ),
        truncated=truncated,
        elapsed_ms=1.0,
        label="04_schema_context",
    )
