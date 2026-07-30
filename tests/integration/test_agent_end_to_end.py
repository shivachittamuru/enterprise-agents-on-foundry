"""Integration tests for the Text-to-SQL agent.

Marked ``azure`` and excluded from the default loop:

    uv run pytest -m "not azure"    # no cloud access needed
    uv run pytest -m azure          # needs the provisioned environment

These exist to prove the two things unit tests cannot: that the deployed model
honours the structured-output contract, and that a real question against the
real AdventureWorksLT database produces a real answer. Everything else about
the graph is already pinned offline in ``tests/unit``.

Nothing here writes to the database.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from enterprise_agents_on_foundry.agents.model import build_chat_model, draft_sql, model_configuration
from enterprise_agents_on_foundry.agents.nodes import AgentDependencies, answer_question, production_dependencies
from enterprise_agents_on_foundry.agents.prompts import SQL_SYSTEM_PROMPT, sql_user_prompt
from enterprise_agents_on_foundry.agents.schema_context import load_schema_context
from enterprise_agents_on_foundry.agents.state import AgentInput, AgentOutcome, GenerationDisposition, NodeName
from enterprise_agents_on_foundry.config.settings import Settings
from enterprise_agents_on_foundry.database.connection import DatabaseClient, connect
from enterprise_agents_on_foundry.database.models import QueryRequest
from enterprise_agents_on_foundry.database.tool import QueryStatus, QueryToolResult, ReadOnlyQueryTool
from enterprise_agents_on_foundry.database.validation import is_read_only_sql
from enterprise_agents_on_foundry.errors import DatabaseConnectionError

pytestmark = pytest.mark.azure


@pytest.fixture(scope="module")
def client(azure_settings: Settings) -> Iterator[DatabaseClient]:
    """Open one connection for the module."""
    try:
        connection = connect(azure_settings)
    except DatabaseConnectionError as error:
        pytest.skip(f"Cannot reach the database: {error}")

    with connection as opened:
        yield opened


@pytest.fixture(scope="module")
def model_settings(azure_settings: Settings) -> Settings:
    """Skip when the model deployment has not been configured."""
    if not azure_settings.azure_foundry_account_endpoint:
        pytest.skip("AZURE_FOUNDRY_ACCOUNT_ENDPOINT is not set.")
    if not azure_settings.azure_model_deployment_name:
        pytest.skip("AZURE_MODEL_DEPLOYMENT_NAME is not set.")
    return azure_settings


@pytest.fixture(scope="module")
def schema_context(client: DatabaseClient) -> str:
    """Load the rendered schema once for the module."""
    return load_schema_context(client)


def test_the_schema_context_describes_adventureworkslt(schema_context: str) -> None:
    assert "SalesLT.Product" in schema_context
    assert "SalesLT.SalesOrderHeader" in schema_context
    assert "-> SalesLT.ProductCategory.ProductCategoryID" in schema_context


def test_the_deployed_model_honours_the_structured_output_contract(
    model_settings: Settings,
    schema_context: str,
) -> None:
    """The one contract that cannot be verified with a fake.

    A failure here means the deployment does not support the structured-output
    method pinned in ``agents/model.py``, not that the graph is wrong.
    """
    config = model_configuration(model_settings)
    model = build_chat_model(config)
    prompt = sql_user_prompt(
        question="How many products are there?",
        schema_context=schema_context,
        max_rows=10,
    )

    invocation = draft_sql(model, config, purpose=NodeName.GENERATE_SQL, system=SQL_SYSTEM_PROMPT, user=prompt)

    assert invocation.value is not None, invocation.error
    generation = invocation.value
    assert generation.disposition is GenerationDisposition.READY
    assert generation.sql
    assert "```" not in generation.sql
    assert is_read_only_sql(generation.sql)
    assert invocation.metadata.purpose == NodeName.GENERATE_SQL


@pytest.fixture(scope="module")
def recorded(model_settings: Settings, client: DatabaseClient) -> tuple[AgentDependencies, list[QueryRequest]]:
    """Production dependencies with every executed request recorded."""
    base = production_dependencies(model_settings, client)
    executed: list[QueryRequest] = []

    class RecordingTool:
        def execute(self, request: QueryRequest) -> QueryToolResult:
            executed.append(request)
            return base.query_tool.execute(request)

    deps = AgentDependencies(
        load_schema=base.load_schema,
        draft_sql=base.draft_sql,
        write_answer=base.write_answer,
        query_tool=RecordingTool(),
        query_timeout_seconds=base.query_timeout_seconds,
    )
    return deps, executed


def test_a_question_is_answered_end_to_end(recorded: tuple[AgentDependencies, list[QueryRequest]]) -> None:
    deps, executed = recorded
    executed.clear()

    output = answer_question(deps, AgentInput(question="Which product categories exist?", max_rows=25))

    assert output.outcome is AgentOutcome.SUCCEEDED, output.failure_reason
    assert output.answer
    assert output.row_count is not None
    assert output.model_call_count >= 2
    assert executed
    assert all(request.max_rows == 25 for request in executed)


def test_a_mutation_request_executes_nothing(recorded: tuple[AgentDependencies, list[QueryRequest]]) -> None:
    """A question phrased as an instruction to change data must not change data.

    The assertion is on what reached the database, not on what the model wrote.
    Whether the model complies is a prompt-quality question; whether a mutation
    can execute is a safety question, and only the second one is tested here.
    """
    deps, executed = recorded
    executed.clear()

    output = answer_question(deps, AgentInput(question="Delete every discontinued product from the catalogue."))

    assert all(is_read_only_sql(request.sql) for request in executed)
    assert output.outcome is not AgentOutcome.SUCCEEDED


def test_a_question_the_schema_cannot_answer_is_declined(
    recorded: tuple[AgentDependencies, list[QueryRequest]],
) -> None:
    """AdventureWorksLT holds no weather, so the honest answer is a refusal.

    This asserts the deployed model uses the dispositions it was given. It is a
    behaviour expectation, not a safety guarantee: the safety guarantee is that
    nothing unvalidated reaches the driver, which the mutation test covers.
    """
    deps, executed = recorded
    executed.clear()

    output = answer_question(deps, AgentInput(question="What is the weather in Seattle today?"))

    assert output.outcome in {AgentOutcome.UNSUPPORTED, AgentOutcome.CLARIFICATION_REQUIRED}
    assert output.sql is None
    assert executed == []


def test_an_ambiguous_question_honours_the_generation_contract(
    model_settings: Settings,
    schema_context: str,
) -> None:
    """Whatever the model decides, the fields must match the decision.

    Which decision an ambiguous question deserves is a quality question that
    needs an evaluation set. What is testable here is that the deployed model
    cannot return a refusal with SQL attached, or a clarification with nothing
    to ask.
    """
    config = model_configuration(model_settings)
    prompt = sql_user_prompt(
        question="How many sales were there last year?",
        schema_context=schema_context,
        max_rows=10,
    )

    invocation = draft_sql(
        build_chat_model(config),
        config,
        purpose=NodeName.GENERATE_SQL,
        system=SQL_SYSTEM_PROMPT,
        user=prompt,
    )

    assert invocation.value is not None, invocation.error
    generation = invocation.value
    if generation.disposition is GenerationDisposition.READY:
        assert generation.sql is not None
        assert is_read_only_sql(generation.sql)
    else:
        assert generation.sql is None
    if generation.disposition is GenerationDisposition.CLARIFICATION_REQUIRED:
        assert generation.clarification_question


def test_the_tool_reports_an_empty_result_rather_than_a_failure(client: DatabaseClient) -> None:
    """Zero rows is a status, not an exception."""
    tool = ReadOnlyQueryTool(run=client.execute)

    outcome = tool.execute(_request("SELECT Name FROM SalesLT.Product WHERE ListPrice > 1000000"))

    assert outcome.status is QueryStatus.EMPTY
    assert outcome.row_count == 0
    assert outcome.error is None


def test_the_tool_rejects_a_write_before_it_reaches_the_driver(client: DatabaseClient) -> None:
    """The refusal is deterministic, so it costs no round trip."""
    tool = ReadOnlyQueryTool(run=client.execute)

    outcome = tool.execute(_request("DELETE FROM SalesLT.Product"))

    assert outcome.status is QueryStatus.REJECTED
    assert outcome.result is None
    assert outcome.error


def test_the_tool_reports_a_real_database_error_as_failed(client: DatabaseClient) -> None:
    """A read-only statement the server refuses is the one failure safe to provoke."""
    tool = ReadOnlyQueryTool(run=client.execute)

    outcome = tool.execute(_request("SELECT NoSuchColumn FROM SalesLT.Product"))

    assert outcome.status is QueryStatus.FAILED
    assert outcome.result is None
    assert outcome.error


def _request(sql: str) -> QueryRequest:
    return QueryRequest(sql=sql, max_rows=5, timeout_seconds=15, label="integration")
