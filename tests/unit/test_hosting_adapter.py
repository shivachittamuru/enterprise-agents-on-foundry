"""Behaviour of the Responses adapter, exercised without Azure or the host SDK.

The adapter is thin, so these tests state what the agent core returned and
assert what the caller would see. The compiled adapter graph is invoked
directly with plain messages; nothing here imports the Foundry hosting server or
touches a network.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from enterprise_agents_on_foundry.agents.model import ModelInvocation
from enterprise_agents_on_foundry.agents.nodes import AgentDependencies, answer_question
from enterprise_agents_on_foundry.agents.state import (
    AgentInput,
    AgentOutcome,
    AgentOutput,
    GenerationDisposition,
    ModelCallMetadata,
    SqlGenerationResult,
)
from enterprise_agents_on_foundry.database.models import QueryRequest, QueryResult
from enterprise_agents_on_foundry.database.tool import ReadOnlyQueryTool
from enterprise_agents_on_foundry.hosting.responses import (
    CLARIFICATION_FALLBACK,
    FAILED_MESSAGE,
    REJECTED_MESSAGE,
    UNSUPPORTED_INPUT_MESSAGE,
    UNSUPPORTED_QUESTION_MESSAGE,
    build_adapter_graph,
    extract_user_text,
    render_output,
)

GOOD_SQL = "SELECT TOP (10) Name FROM SalesLT.Product"
UNSAFE_SQL = "DELETE FROM SalesLT.Product"
SCHEMA = "SalesLT.Product\n  Name nvarchar not null"
ANSWER = "There are three products."


def _meta() -> ModelCallMetadata:
    return ModelCallMetadata(purpose="test", latency_ms=1.0)


def _ready(sql: str = GOOD_SQL) -> SqlGenerationResult:
    return SqlGenerationResult(disposition=GenerationDisposition.READY, sql=sql, rationale="ok")


class _FakeModel:
    """Replays scripted drafts and one answer, like the node-level fakes."""

    def __init__(self, drafts: list[SqlGenerationResult], answer: str = ANSWER) -> None:
        self._drafts = list(drafts)
        self._answer = answer

    def draft(self, purpose: str, system: str, user: str) -> ModelInvocation[SqlGenerationResult]:
        return ModelInvocation(metadata=_meta(), value=self._drafts.pop(0))

    def write(self, purpose: str, system: str, user: str) -> ModelInvocation[str]:
        return ModelInvocation(metadata=_meta(), value=self._answer)


def _rows(count: int) -> QueryResult:
    return QueryResult(
        columns=("Name",),
        rows=tuple(("Bike",) for _ in range(count)),
        truncated=False,
        elapsed_ms=1.0,
        label="agent",
    )


def _run_success(request: QueryRequest) -> QueryResult:
    return _rows(3)


def _deps(
    *,
    model: _FakeModel | None = None,
    run: object = None,
    schema: str = SCHEMA,
) -> AgentDependencies:
    fake_model = model or _FakeModel([_ready()])
    tool = ReadOnlyQueryTool(run=run or _run_success)  # type: ignore[arg-type]
    return AgentDependencies(
        load_schema=lambda: schema,
        draft_sql=fake_model.draft,
        write_answer=fake_model.write,
        query_tool=tool,
    )


def _last_text(final: dict[str, list[BaseMessage]]) -> str:
    message = final["messages"][-1]
    assert isinstance(message, AIMessage)
    assert isinstance(message.content, str)
    return message.content


class TestExtractUserText:
    """The latest human turn is the question; anything else is unsupported."""

    def test_returns_latest_human_text(self) -> None:
        messages = [AIMessage(content="earlier"), HumanMessage(content="how many products?")]
        assert extract_user_text(messages) == "how many products?"

    def test_non_text_content_is_unsupported(self) -> None:
        messages = [HumanMessage(content=[{"type": "image_url", "image_url": "http://x"}])]
        assert extract_user_text(messages) is None

    def test_no_human_message_is_unsupported(self) -> None:
        assert extract_user_text([AIMessage(content="only assistant")]) is None

    def test_blank_text_is_unsupported(self) -> None:
        assert extract_user_text([HumanMessage(content="   ")]) is None


class TestRenderOutput:
    """Each outcome renders to a distinct, controlled sentence."""

    def test_success_returns_the_answer(self) -> None:
        output = AgentOutput(outcome=AgentOutcome.SUCCEEDED, question="q", answer=ANSWER)
        assert render_output(output) == ANSWER

    def test_empty_returns_the_answer(self) -> None:
        output = AgentOutput(outcome=AgentOutcome.EMPTY, question="q", answer="Nothing matched.")
        assert render_output(output) == "Nothing matched."

    def test_clarification_returns_the_question(self) -> None:
        output = AgentOutput(
            outcome=AgentOutcome.CLARIFICATION_REQUIRED,
            question="q",
            clarification_question="Which year did you mean?",
        )
        assert render_output(output) == "Which year did you mean?"

    def test_clarification_without_a_question_falls_back(self) -> None:
        output = AgentOutput(outcome=AgentOutcome.CLARIFICATION_REQUIRED, question="q")
        assert render_output(output) == CLARIFICATION_FALLBACK

    def test_unsupported_returns_the_controlled_message(self) -> None:
        output = AgentOutput(outcome=AgentOutcome.UNSUPPORTED, question="q")
        assert render_output(output) == UNSUPPORTED_QUESTION_MESSAGE

    def test_rejected_returns_the_controlled_message(self) -> None:
        output = AgentOutput(outcome=AgentOutcome.REJECTED, question="q", failure_reason="not a SELECT")
        assert render_output(output) == REJECTED_MESSAGE

    def test_failed_returns_the_controlled_message(self) -> None:
        output = AgentOutput(outcome=AgentOutcome.FAILED, question="q", failure_reason="boom")
        assert render_output(output) == FAILED_MESSAGE

    def test_internal_reason_never_reaches_the_caller(self) -> None:
        leaked = "Server=tcp:db;Uid=admin;Pwd=hunter2;token=eyJabc123"
        rejected = AgentOutput(outcome=AgentOutcome.REJECTED, question="q", failure_reason=leaked, sql=UNSAFE_SQL)
        failed = AgentOutput(outcome=AgentOutcome.FAILED, question="q", failure_reason=leaked)
        for rendered in (render_output(rejected), render_output(failed)):
            assert "Pwd" not in rendered
            assert "hunter2" not in rendered
            assert "token" not in rendered
            assert UNSAFE_SQL not in rendered


class TestAdapterGraph:
    """The compiled graph maps a Responses turn to one assistant message."""

    def _invoke(self, deps: AgentDependencies, message: BaseMessage) -> str:
        graph = build_adapter_graph(deps)
        final = graph.invoke({"messages": [message]})
        return _last_text(final)

    def test_successful_question_returns_the_answer(self) -> None:
        text = self._invoke(_deps(), HumanMessage(content="how many products?"))
        assert text == ANSWER

    def test_unsupported_input_is_reported(self) -> None:
        message = HumanMessage(content=[{"type": "image_url", "image_url": "http://x"}])
        text = self._invoke(_deps(), message)
        assert text == UNSUPPORTED_INPUT_MESSAGE

    def test_rejected_sql_is_reported(self) -> None:
        model = _FakeModel([_ready(UNSAFE_SQL), _ready(UNSAFE_SQL)])
        text = self._invoke(_deps(model=model), HumanMessage(content="delete the products"))
        assert text == REJECTED_MESSAGE

    def test_unexpected_error_is_contained(self) -> None:
        def _raise() -> str:
            raise RuntimeError("Server=tcp:db;Pwd=hunter2")

        deps = AgentDependencies(
            load_schema=_raise,
            draft_sql=_deps().draft_sql,
            write_answer=_deps().write_answer,
            query_tool=_deps().query_tool,
        )
        text = self._invoke(deps, HumanMessage(content="how many products?"))
        assert text == FAILED_MESSAGE
        assert "hunter2" not in text


class TestCoreRemainsUsable:
    """The existing graph API works without the protocol host."""

    def test_answer_question_runs_standalone(self) -> None:
        output = answer_question(_deps(), AgentInput(question="how many products?"))
        assert output.outcome is AgentOutcome.SUCCEEDED
        assert output.answer == ANSWER


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
