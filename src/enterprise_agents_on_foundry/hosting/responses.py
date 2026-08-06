"""The Responses-protocol adapter.

The Foundry Responses host expects a compiled LangGraph graph whose state has a
``messages`` field. The production Text-to-SQL graph deliberately does not use
that shape, so this module wraps it in a one-node ``MessagesState`` graph rather
than rewriting the core around a message transcript.

The single node does only the mapping the protocol requires:

    latest user text
    -> AgentInput
    -> answer_question (the existing graph API)
    -> AgentOutput
    -> one assistant message

Every terminal outcome is rendered to a controlled sentence. A refusal, an
unsupported question, and an unexpected error all become fixed text, so an
internal reason, a driver error, a token, or a connection string can never reach
the caller through this boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from enterprise_agents_on_foundry.agents.nodes import AgentDependencies, answer_question
from enterprise_agents_on_foundry.agents.state import AgentInput, AgentOutcome, AgentOutput

UNSUPPORTED_INPUT_MESSAGE = "Only text questions are supported. Please send your question as text."
"""Returned when a turn carries no usable text, such as an image or audio part."""

UNSUPPORTED_QUESTION_MESSAGE = "That question can't be answered with the available data."
REJECTED_MESSAGE = "That request was rejected because it was not a safe read-only query."
FAILED_MESSAGE = "The question could not be answered because of an unexpected error."
CLARIFICATION_FALLBACK = "Could you clarify your question so it can be answered precisely?"
EMPTY_FALLBACK = "No rows matched your question."

__all__ = [
    "CLARIFICATION_FALLBACK",
    "EMPTY_FALLBACK",
    "FAILED_MESSAGE",
    "REJECTED_MESSAGE",
    "UNSUPPORTED_INPUT_MESSAGE",
    "UNSUPPORTED_QUESTION_MESSAGE",
    "build_adapter_graph",
    "extract_user_text",
    "render_output",
]


def extract_user_text(messages: Sequence[BaseMessage]) -> str | None:
    """Return the current turn's user text, or ``None`` when it is unsupported.

    The host sends prior response history followed by the current input, so the
    latest human message is the question. Non-text content (a list of parts such
    as an image) and a blank message are treated as unsupported input rather than
    coerced into a question.
    """
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content = message.content
            if isinstance(content, str) and content.strip():
                return content
            return None
    return None


def render_output(output: AgentOutput) -> str:
    """Map a terminal :class:`AgentOutput` onto one caller-facing sentence.

    Distinct outcomes stay distinct, but refusals and failures render to fixed
    text so no internal reason is exposed.
    """
    if output.outcome in (AgentOutcome.SUCCEEDED, AgentOutcome.EMPTY):
        return output.answer or EMPTY_FALLBACK
    if output.outcome is AgentOutcome.CLARIFICATION_REQUIRED:
        return output.clarification_question or CLARIFICATION_FALLBACK
    if output.outcome is AgentOutcome.UNSUPPORTED:
        return UNSUPPORTED_QUESTION_MESSAGE
    if output.outcome is AgentOutcome.REJECTED:
        return REJECTED_MESSAGE
    return FAILED_MESSAGE


def build_adapter_graph(deps: AgentDependencies) -> Any:
    """Compile the one-node ``MessagesState`` graph the Responses host runs.

    Returns ``Any`` for the same reason :func:`compile_graph` does: the compiled
    type is an implementation detail the callers never inspect.
    """

    def respond(state: MessagesState) -> dict[str, list[BaseMessage]]:
        text = extract_user_text(state["messages"])
        if text is None:
            return {"messages": [AIMessage(content=UNSUPPORTED_INPUT_MESSAGE)]}
        try:
            output = answer_question(deps, AgentInput(question=text))
        except Exception:  # a protocol boundary reports a controlled failure, never a trace
            return {"messages": [AIMessage(content=FAILED_MESSAGE)]}
        return {"messages": [AIMessage(content=render_output(output))]}

    builder: StateGraph[MessagesState, None, MessagesState, MessagesState] = StateGraph(MessagesState)
    builder.add_node("respond", respond)
    builder.add_edge(START, "respond")
    builder.add_edge("respond", END)
    return builder.compile()
