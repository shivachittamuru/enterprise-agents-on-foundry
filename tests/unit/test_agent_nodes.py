"""Behaviour of the production nodes, exercised without Azure.

Every dependency is a plain function, so these tests state what the model
returned and what the database did, then assert what the graph decided. The
legacy agent had no equivalent: its behaviour could only be observed by calling
a live model against a live database.
"""

from __future__ import annotations

import pytest

from enterprise_agents_on_foundry.agents.graph import compile_graph
from enterprise_agents_on_foundry.agents.model import ModelInvocation
from enterprise_agents_on_foundry.agents.nodes import (
    QUERY_LABEL,
    RECURSION_LIMIT,
    AgentDependencies,
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
    AgentOutcome,
    FailureStage,
    GenerationDisposition,
    ModelCallMetadata,
    NodeName,
    SqlGenerationResult,
    initial_state,
)
from enterprise_agents_on_foundry.database.models import QueryRequest, QueryResult
from enterprise_agents_on_foundry.database.tool import QueryStatus, QueryToolResult, ReadOnlyQueryTool
from enterprise_agents_on_foundry.errors import DatabaseConnectionError, QueryValidationError

GOOD_SQL = "SELECT TOP (10) Name FROM SalesLT.Product"
UNSAFE_SQL = "DELETE FROM SalesLT.Product"
SCHEMA = "SalesLT.Product\n  Name nvarchar not null"


def _ready(sql: str = GOOD_SQL) -> SqlGenerationResult:
    return SqlGenerationResult(disposition=GenerationDisposition.READY, sql=sql, rationale="because")


def _clarification(question: str = "Which year did you mean?") -> SqlGenerationResult:
    return SqlGenerationResult(
        disposition=GenerationDisposition.CLARIFICATION_REQUIRED,
        clarification_question=question,
    )


def _unsupported() -> SqlGenerationResult:
    return SqlGenerationResult(
        disposition=GenerationDisposition.UNSUPPORTED,
        rationale="The schema holds no weather data.",
    )


class FakeModel:
    """Records prompts and replays scripted invocations.

    A scripted string stands for a call the model boundary could not turn into a
    usable value, because that is a returned failure now rather than an exception.
    """

    def __init__(
        self,
        drafts: list[SqlGenerationResult | str],
        answer: str = "An answer.",
        *,
        answer_error: str | None = None,
    ) -> None:
        self._drafts = list(drafts)
        self._answer = answer
        self._answer_error = answer_error
        self.draft_purposes: list[str] = []
        self.draft_systems: list[str] = []
        self.draft_users: list[str] = []
        self.answer_systems: list[str] = []

    def draft(self, purpose: str, system: str, user: str) -> ModelInvocation[SqlGenerationResult]:
        self.draft_purposes.append(purpose)
        self.draft_systems.append(system)
        self.draft_users.append(user)
        if not self._drafts:
            raise AssertionError("the model was asked for more drafts than the test scripted")
        response = self._drafts.pop(0)
        if isinstance(response, str):
            return ModelInvocation(metadata=_metadata(purpose), error=response)
        return ModelInvocation(metadata=_metadata(purpose), value=response)

    def write(self, purpose: str, system: str, user: str) -> ModelInvocation[str]:
        self.answer_systems.append(system)
        if self._answer_error is not None:
            return ModelInvocation(metadata=_metadata(purpose), error=self._answer_error)
        return ModelInvocation(metadata=_metadata(purpose), value=self._answer)


class FakeQueryTool:
    """Records requests and replays scripted tool results."""

    def __init__(self, outcomes: list[QueryToolResult] | None = None) -> None:
        self._outcomes = list(outcomes or [_succeeded()])
        self.requests: list[QueryRequest] = []

    def execute(self, request: QueryRequest) -> QueryToolResult:
        self.requests.append(request)
        if not self._outcomes:
            raise AssertionError("the tool was called more times than the test scripted")
        return self._outcomes.pop(0)


def _metadata(purpose: str) -> ModelCallMetadata:
    """Metadata a deployment might return without usage accounting."""
    return ModelCallMetadata(purpose=purpose, latency_ms=1.0)


def _result(rows: tuple[tuple[object, ...], ...] = (("Bike",),), *, truncated: bool = False) -> QueryResult:
    return QueryResult(
        columns=("Name",),
        rows=rows,
        truncated=truncated,
        elapsed_ms=1.0,
        label=QUERY_LABEL,
    )


def _succeeded(result: QueryResult | None = None) -> QueryToolResult:
    return QueryToolResult(status=QueryStatus.SUCCESS, result=result or _result(), elapsed_ms=1.0)


def _empty() -> QueryToolResult:
    return QueryToolResult(status=QueryStatus.EMPTY, result=_result(rows=()), elapsed_ms=1.0)


def _rejected(error: str = "Query contains 2 statements") -> QueryToolResult:
    return QueryToolResult(status=QueryStatus.REJECTED, error=error, elapsed_ms=1.0)


def _failed(error: str = "boom") -> QueryToolResult:
    return QueryToolResult(status=QueryStatus.FAILED, error=error, elapsed_ms=1.0)


def _dependencies(
    *,
    model: FakeModel | None = None,
    tool: FakeQueryTool | None = None,
    schema: str = SCHEMA,
) -> AgentDependencies:
    fake_model = model or FakeModel([_ready()])
    fake_tool = tool or FakeQueryTool()
    return AgentDependencies(
        load_schema=lambda: schema,
        draft_sql=fake_model.draft,
        write_answer=fake_model.write,
        query_tool=fake_tool,
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
        assert output.outcome is AgentOutcome.SUCCEEDED
        assert output.answer == "An answer."
        assert output.sql == GOOD_SQL
        assert output.query_status is QueryStatus.SUCCESS
        assert output.repair_attempts == 0
        assert output.failure_stage is None
        assert output.clarification_question is None

    def test_reports_the_row_count_from_the_result(self) -> None:
        tool = FakeQueryTool([_succeeded(_result(rows=(("Bike",), ("Helmet",))))])
        output = answer_question(_dependencies(tool=tool), AgentInput(question="what products?"))

        assert output.row_count == 2

    def test_calls_the_model_once_for_sql_and_once_for_the_answer(self) -> None:
        model = FakeModel([_ready()])
        output = answer_question(_dependencies(model=model), AgentInput(question="what products?"))

        assert model.draft_systems == [SQL_SYSTEM_PROMPT]
        assert model.answer_systems == [ANSWER_SYSTEM_PROMPT]
        assert output.model_call_count == 2

    def test_records_the_purpose_of_every_model_call(self) -> None:
        """A run reports which calls it made, so cost is attributable per node."""
        output = answer_question(_dependencies(), AgentInput(question="what products?"))

        assert [call.purpose for call in output.model_calls] == [NodeName.GENERATE_SQL, NodeName.COMPOSE_ANSWER]

    def test_token_counts_are_optional(self) -> None:
        """A deployment that reports no usage must not break the run."""
        output = answer_question(_dependencies(), AgentInput(question="what products?"))

        assert output.succeeded
        assert all(call.total_tokens is None for call in output.model_calls)

    def test_passes_the_schema_and_question_to_the_model(self) -> None:
        model = FakeModel([_ready()])
        answer_question(_dependencies(model=model), AgentInput(question="which bikes are red?"))

        assert SCHEMA in model.draft_users[0]
        assert "which bikes are red?" in model.draft_users[0]


class TestNonExecutableOutcomes:
    """Questions the agent declines to turn into SQL."""

    def test_an_ambiguous_question_returns_a_clarification(self) -> None:
        model = FakeModel([_clarification()])
        tool = FakeQueryTool()
        output = answer_question(_dependencies(model=model, tool=tool), AgentInput(question="how many sales?"))

        assert output.outcome is AgentOutcome.CLARIFICATION_REQUIRED
        assert output.clarification_question == "Which year did you mean?"
        assert output.answer is None
        assert output.sql is None

    def test_a_clarification_never_reaches_the_database(self) -> None:
        model = FakeModel([_clarification()])
        tool = FakeQueryTool()
        answer_question(_dependencies(model=model, tool=tool), AgentInput(question="how many sales?"))

        assert tool.requests == []
        assert model.answer_systems == []

    def test_an_unsupported_question_ends_without_sql(self) -> None:
        model = FakeModel([_unsupported()])
        tool = FakeQueryTool()
        output = answer_question(_dependencies(model=model, tool=tool), AgentInput(question="what is the weather?"))

        assert output.outcome is AgentOutcome.UNSUPPORTED
        assert output.sql is None
        assert output.query_status is None
        assert tool.requests == []

    def test_a_declined_question_costs_one_model_call(self) -> None:
        model = FakeModel([_unsupported()])
        output = answer_question(_dependencies(model=model), AgentInput(question="what is the weather?"))

        assert output.model_call_count == 1


class TestExecutionBoundary:
    """What actually reaches the database."""

    def test_applies_the_requested_row_cap_to_the_request(self) -> None:
        tool = FakeQueryTool()
        answer_question(_dependencies(tool=tool), AgentInput(question="what products?", max_rows=7))

        assert tool.requests[0].max_rows == 7

    def test_labels_every_agent_query(self) -> None:
        tool = FakeQueryTool()
        answer_question(_dependencies(tool=tool), AgentInput(question="what products?"))

        assert tool.requests[0].label == QUERY_LABEL

    def test_never_executes_an_unsafe_statement(self) -> None:
        model = FakeModel([_ready(UNSAFE_SQL), _ready(UNSAFE_SQL)])
        tool = FakeQueryTool()
        output = answer_question(_dependencies(model=model, tool=tool), AgentInput(question="delete them"))

        assert tool.requests == []
        assert output.outcome is AgentOutcome.REJECTED
        assert output.failure_stage is FailureStage.VALIDATION

    def test_reports_the_forbidden_keyword_in_the_failure_reason(self) -> None:
        model = FakeModel([_ready(UNSAFE_SQL), _ready(UNSAFE_SQL)])
        output = answer_question(_dependencies(model=model), AgentInput(question="delete them"))

        assert output.failure_reason is not None
        assert "DELETE" in output.failure_reason

    def test_a_query_with_no_matching_rows_is_still_answered(self) -> None:
        """Nothing matched is a true answer, reported as its own outcome."""
        tool = FakeQueryTool([_empty()])
        model = FakeModel([_ready()], answer="No matching records were found.")
        output = answer_question(_dependencies(model=model, tool=tool), AgentInput(question="products over $1bn"))

        assert output.outcome is AgentOutcome.EMPTY
        assert output.succeeded is False
        assert output.answer == "No matching records were found."
        assert output.row_count == 0
        assert output.query_status is QueryStatus.EMPTY

    def test_a_persistently_rejected_query_is_reported_at_the_validation_stage(self) -> None:
        model = FakeModel([_ready(), _ready()])
        tool = FakeQueryTool([_rejected(), _rejected()])
        output = answer_question(_dependencies(model=model, tool=tool), AgentInput(question="what products?"))

        assert output.outcome is AgentOutcome.REJECTED
        assert output.query_status is QueryStatus.REJECTED
        assert output.failure_stage is FailureStage.VALIDATION

    def test_a_persistently_failing_query_is_reported_at_the_execution_stage(self) -> None:
        model = FakeModel([_ready(), _ready()])
        tool = FakeQueryTool([_failed("boom"), _failed("boom again")])
        output = answer_question(_dependencies(model=model, tool=tool), AgentInput(question="what products?"))

        assert output.outcome is AgentOutcome.FAILED
        assert output.query_status is QueryStatus.FAILED
        assert output.failure_stage is FailureStage.EXECUTION
        assert output.failure_reason == "boom again"


class TestRepair:
    """The single correction attempt."""

    def test_repairs_an_invalid_statement_and_then_succeeds(self) -> None:
        model = FakeModel([_ready(UNSAFE_SQL), _ready()])
        output = answer_question(_dependencies(model=model), AgentInput(question="what products?"))

        assert output.succeeded
        assert output.repair_attempts == 1
        assert output.sql == GOOD_SQL
        assert model.draft_systems == [SQL_SYSTEM_PROMPT, REPAIR_SYSTEM_PROMPT]

    def test_gives_the_repair_prompt_the_failed_statement_and_the_error(self) -> None:
        model = FakeModel([_ready(UNSAFE_SQL), _ready()])
        answer_question(_dependencies(model=model), AgentInput(question="what products?"))

        repair_prompt = model.draft_users[1]
        assert UNSAFE_SQL in repair_prompt
        assert "DELETE" in repair_prompt

    def test_repairs_an_execution_failure(self) -> None:
        model = FakeModel([_ready(), _ready()])
        tool = FakeQueryTool([_failed("invalid column"), _succeeded()])
        output = answer_question(_dependencies(model=model, tool=tool), AgentInput(question="what products?"))

        assert output.succeeded
        assert output.repair_attempts == 1

    def test_asks_the_model_for_at_most_two_statements(self) -> None:
        model = FakeModel([_ready(UNSAFE_SQL), _ready(UNSAFE_SQL)])
        output = answer_question(_dependencies(model=model), AgentInput(question="what products?"))

        assert len(model.draft_systems) == 2
        assert output.outcome is AgentOutcome.REJECTED

    def test_a_second_execution_failure_is_terminal(self) -> None:
        model = FakeModel([_ready(), _ready()])
        tool = FakeQueryTool([_failed("boom"), _failed("boom again")])
        output = answer_question(_dependencies(model=model, tool=tool), AgentInput(question="what products?"))

        assert output.outcome is AgentOutcome.FAILED
        assert output.failure_stage is FailureStage.EXECUTION
        assert len(tool.requests) == 2

    def test_a_repair_may_conclude_the_question_is_unsupported(self) -> None:
        model = FakeModel([_ready(UNSAFE_SQL), _unsupported()])
        tool = FakeQueryTool()
        output = answer_question(_dependencies(model=model, tool=tool), AgentInput(question="delete them"))

        assert output.outcome is AgentOutcome.UNSUPPORTED
        assert tool.requests == []


class TestModelFailures:
    """Unusable model output is a reported failure, not an exception."""

    def test_a_failed_draft_ends_the_run_without_a_repair(self) -> None:
        model = FakeModel(["no statement"])
        output = answer_question(_dependencies(model=model), AgentInput(question="what products?"))

        assert output.outcome is AgentOutcome.FAILED
        assert output.failure_stage is FailureStage.GENERATION
        assert output.failure_reason == "no statement"
        assert output.repair_attempts == 1
        assert len(model.draft_systems) == 1

    def test_a_failed_repair_ends_the_run(self) -> None:
        model = FakeModel([_ready(UNSAFE_SQL), "still nothing"])
        output = answer_question(_dependencies(model=model), AgentInput(question="what products?"))

        assert output.outcome is AgentOutcome.FAILED
        assert output.failure_stage is FailureStage.GENERATION
        assert output.failure_reason == "still nothing"

    def test_a_failed_answer_is_reported_at_the_answer_stage(self) -> None:
        model = FakeModel([_ready()], answer_error="empty answer")
        output = answer_question(_dependencies(model=model), AgentInput(question="what products?"))

        assert output.outcome is AgentOutcome.FAILED
        assert output.failure_stage is FailureStage.ANSWER
        assert output.answer is None


class TestQueryTool:
    """The tool that stands between the graph and the database."""

    def test_a_result_with_rows_is_a_success(self) -> None:
        outcome = ReadOnlyQueryTool(run=lambda request: _result()).execute(_request())

        assert outcome.status is QueryStatus.SUCCESS
        assert outcome.row_count == 1

    def test_a_result_with_no_rows_is_empty_rather_than_failed(self) -> None:
        outcome = ReadOnlyQueryTool(run=lambda request: _result(rows=())).execute(_request())

        assert outcome.status is QueryStatus.EMPTY
        assert outcome.error is None

    def test_a_refused_statement_is_rejected(self) -> None:
        def refuse(request: QueryRequest) -> QueryResult:
            raise QueryValidationError("Query contains 2 statements")

        outcome = ReadOnlyQueryTool(run=refuse).execute(_request())

        assert outcome.status is QueryStatus.REJECTED
        assert outcome.result is None
        assert outcome.error == "Query contains 2 statements"

    def test_a_database_error_is_a_failure(self) -> None:
        def explode(request: QueryRequest) -> QueryResult:
            raise DatabaseConnectionError("the database is unreachable")

        outcome = ReadOnlyQueryTool(run=explode).execute(_request())

        assert outcome.status is QueryStatus.FAILED
        assert outcome.error == "the database is unreachable"

    def test_every_outcome_is_timed(self) -> None:
        outcome = ReadOnlyQueryTool(run=lambda request: _result()).execute(_request())

        assert outcome.elapsed_ms >= 0.0

    def test_a_success_must_carry_a_result(self) -> None:
        with pytest.raises(ValueError, match="result"):
            QueryToolResult(status=QueryStatus.SUCCESS)

    def test_a_success_with_no_rows_is_a_contradiction(self) -> None:
        with pytest.raises(ValueError, match="row count"):
            QueryToolResult(status=QueryStatus.SUCCESS, result=_result(rows=()))

    def test_a_failure_must_carry_a_reason(self) -> None:
        with pytest.raises(ValueError, match="reason"):
            QueryToolResult(status=QueryStatus.FAILED)


def _request(sql: str = GOOD_SQL) -> QueryRequest:
    return QueryRequest(sql=sql, max_rows=10, timeout_seconds=5, label=QUERY_LABEL)


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
        assert update["query_status"] is QueryStatus.FAILED

    def test_a_rejected_query_is_reported_at_the_validation_stage(self) -> None:
        tool = FakeQueryTool([_rejected("Query contains 2 statements")])
        nodes = production_nodes(_dependencies(tool=tool))
        update = nodes.execute_sql(_state(sql=GOOD_SQL))  # type: ignore[arg-type]

        assert update["query_status"] is QueryStatus.REJECTED
        assert update["failure_stage"] is FailureStage.VALIDATION

    def test_load_schema_failures_are_not_swallowed(self) -> None:
        def broken_loader() -> str:
            raise DatabaseConnectionError("the database is unreachable")

        deps = AgentDependencies(
            load_schema=broken_loader,
            draft_sql=FakeModel([]).draft,
            write_answer=FakeModel([]).write,
            query_tool=FakeQueryTool(),
        )
        with pytest.raises(DatabaseConnectionError):
            answer_question(deps, AgentInput(question="what products?"))

    def test_the_recursion_limit_exceeds_the_longest_path(self) -> None:
        assert RECURSION_LIMIT >= 9

    def test_the_graph_compiles_from_the_production_nodes(self) -> None:
        compiled = compile_graph(production_nodes(_dependencies()))

        assert set(compiled.get_graph().nodes) >= {"load_schema", "finalize"}


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
