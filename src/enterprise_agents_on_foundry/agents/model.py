"""The model boundary.

One place builds a chat client, and one place turns a model response into a
typed value. Nodes never touch the SDK, which is why the graph can be tested
without a network call and why swapping the client is a single-file change.

Two properties of the deployment are recorded here as constants rather than as
settings, because they are facts about a model version that should be changed
with a reviewed diff, not tuned per environment.
"""

from __future__ import annotations

from typing import Any, Final

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI
from pydantic import ValidationError

from enterprise_agents_on_foundry.agents.state import SqlDraft
from enterprise_agents_on_foundry.config.settings import Settings
from enterprise_agents_on_foundry.errors import ConfigurationError, ModelOutputError

COGNITIVE_SERVICES_SCOPE: Final = "https://cognitiveservices.azure.com/.default"

STRUCTURED_OUTPUT_METHOD: Final = "json_schema"
"""Constrained decoding against the schema, not a request to please return JSON.

The legacy agent asked for JSON in the prompt and then parsed whatever came
back. Schema-constrained output moves that from hope to a server-side guarantee.
"""

SUPPORTS_TEMPERATURE: Final = False
"""Whether the deployment accepts an explicit sampling temperature.

Recent reasoning-oriented deployments reject any value other than the default
and fail the whole request rather than ignoring the parameter. Set this to True
only after confirming the deployment accepts it.
"""

MAX_MODEL_RETRIES: Final = 2
"""Transport retries only. A rejected or malformed answer is not retried here;
that is the graph's single repair attempt, which is visible in state.
"""

__all__ = [
    "COGNITIVE_SERVICES_SCOPE",
    "MAX_MODEL_RETRIES",
    "STRUCTURED_OUTPUT_METHOD",
    "SUPPORTS_TEMPERATURE",
    "build_chat_model",
    "draft_sql",
    "write_answer",
]


def build_chat_model(settings: Settings) -> BaseChatModel:
    """Build a chat client for the configured Foundry deployment.

    Authentication is Microsoft Entra through a token provider, so no key is
    read, stored, or logged. The token provider refreshes on its own schedule;
    nothing here caches or inspects a token.

    Raises:
        ConfigurationError: when the endpoint or deployment name is missing.
    """
    endpoint = (settings.azure_foundry_account_endpoint or "").strip()
    deployment = (settings.azure_model_deployment_name or "").strip()

    missing = [
        name
        for name, value in (
            ("AZURE_FOUNDRY_ACCOUNT_ENDPOINT", endpoint),
            ("AZURE_MODEL_DEPLOYMENT_NAME", deployment),
        )
        if not value
    ]
    if missing:
        raise ConfigurationError(f"The model client needs {' and '.join(missing)} to be set.")

    token_provider = get_bearer_token_provider(DefaultAzureCredential(), COGNITIVE_SERVICES_SCOPE)
    optional: dict[str, Any] = {"temperature": 0} if SUPPORTS_TEMPERATURE else {}

    return AzureChatOpenAI(
        azure_endpoint=endpoint,
        azure_deployment=deployment,
        api_version=settings.azure_openai_api_version,
        azure_ad_token_provider=token_provider,
        max_retries=MAX_MODEL_RETRIES,
        **optional,
    )


def draft_sql(model: BaseChatModel, *, system: str, user: str) -> SqlDraft:
    """Ask the model for one statement and return it as a typed value.

    Raises:
        ModelOutputError: when the response does not satisfy ``SqlDraft``.
    """
    structured = model.with_structured_output(SqlDraft, method=STRUCTURED_OUTPUT_METHOD, strict=True)
    try:
        response = structured.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    except (ValidationError, OutputParserException) as error:
        raise ModelOutputError(f"The model did not return a usable statement: {error}") from error

    if not isinstance(response, SqlDraft):
        raise ModelOutputError(f"Expected a SqlDraft from the model, received {type(response).__name__}.")

    # Rejected rather than stripped. The legacy agent removed ``` fences from
    # model output before executing it, which silently accepted a response that
    # had ignored the requested format. With structured output a fence means
    # something is wrong upstream, and that should be visible.
    if "```" in response.sql:
        raise ModelOutputError("The model returned a fenced code block instead of a bare statement.")

    return response


def write_answer(model: BaseChatModel, *, system: str, user: str) -> str:
    """Ask the model to describe an already-retrieved result in plain language.

    Raises:
        ModelOutputError: when the response carries no text.
    """
    response = model.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    text = _message_text(response).strip()
    if not text:
        raise ModelOutputError("The model returned an empty answer.")
    return text


def _message_text(message: BaseMessage) -> str:
    """Extract text from a response whose content may be a string or blocks."""
    content = message.content
    if isinstance(content, str):
        return content

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)
