"""The model boundary.

One place builds a chat client, and one place turns a model response into a
typed value. Nodes never touch the SDK, which is why the graph can be tested
without a network call and why swapping the client is a single-file change.

The deployment's behaviour is resolved once into a :class:`ModelConfiguration`
and then passed explicitly. Two of its properties are defaulted from constants
rather than from settings, because they are facts about a model version that
should be changed with a reviewed diff, not tuned per environment.

Every call returns a :class:`ModelInvocation`: a value or a reason, always with
what the call cost. A model that answers unusably is a result here, not an
exception, because the graph has to route on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI
from pydantic import ValidationError

from enterprise_agents_on_foundry.agents.state import ModelCallMetadata, SqlGenerationResult
from enterprise_agents_on_foundry.config.settings import Settings
from enterprise_agents_on_foundry.errors import ConfigurationError
from enterprise_agents_on_foundry.observability.timing import Stopwatch

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
    "ModelConfiguration",
    "ModelInvocation",
    "build_chat_model",
    "draft_sql",
    "model_configuration",
    "write_answer",
]


@dataclass(frozen=True, slots=True)
class ModelConfiguration:
    """The deployment this project is allowed to call, resolved once.

    One deployment, no fallback list and no routing policy. Choosing between
    models is a later release, and pretending to support it now would mean
    designing a selection policy before anything needs one.
    """

    endpoint: str
    deployment: str
    api_version: str
    structured_output_method: str = STRUCTURED_OUTPUT_METHOD
    supports_temperature: bool = SUPPORTS_TEMPERATURE
    max_retries: int = MAX_MODEL_RETRIES


@dataclass(frozen=True, slots=True)
class ModelInvocation[T]:
    """One model call: a typed value or a reason, and always the metadata.

    Metadata survives failure deliberately. A call that produced nothing usable
    still took time and still consumed tokens, and dropping it would make a bad
    answer look free.
    """

    metadata: ModelCallMetadata
    value: T | None = None
    error: str | None = None


def model_configuration(settings: Settings) -> ModelConfiguration:
    """Resolve the configured deployment.

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

    return ModelConfiguration(
        endpoint=endpoint,
        deployment=deployment,
        api_version=settings.azure_openai_api_version,
    )


def build_chat_model(config: ModelConfiguration) -> BaseChatModel:
    """Build a chat client for the configured Foundry deployment.

    Authentication is Microsoft Entra through a token provider, so no key is
    read, stored, or logged. The token provider refreshes on its own schedule;
    nothing here caches or inspects a token.
    """
    token_provider = get_bearer_token_provider(DefaultAzureCredential(), COGNITIVE_SERVICES_SCOPE)
    optional: dict[str, Any] = {"temperature": 0} if config.supports_temperature else {}

    return AzureChatOpenAI(
        azure_endpoint=config.endpoint,
        azure_deployment=config.deployment,
        api_version=config.api_version,
        azure_ad_token_provider=token_provider,
        max_retries=config.max_retries,
        **optional,
    )


def draft_sql(
    model: BaseChatModel,
    config: ModelConfiguration,
    *,
    purpose: str,
    system: str,
    user: str,
) -> ModelInvocation[SqlGenerationResult]:
    """Ask the model what it can do with the question, and return that as a value.

    The raw response is requested alongside the parsed one so that token usage
    can be read without loosening the schema the parsed value is validated
    against.
    """
    structured = model.with_structured_output(
        SqlGenerationResult,
        method=config.structured_output_method,
        strict=True,
        include_raw=True,
    )
    stopwatch = Stopwatch()
    try:
        response = structured.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    except (ValidationError, OutputParserException) as error:
        return _failed(config, purpose, stopwatch, f"The model did not return a usable response: {error}")

    if not isinstance(response, dict):
        return _failed(
            config, purpose, stopwatch, f"Expected a raw and parsed response, received {type(response).__name__}."
        )

    metadata = _metadata(config, purpose, stopwatch, response.get("raw"))
    parsed = response.get("parsed")
    if not isinstance(parsed, SqlGenerationResult):
        reason = response.get("parsing_error") or f"received {type(parsed).__name__}"
        return ModelInvocation(metadata=metadata, error=f"The model did not return a usable response: {reason}")

    # Rejected rather than stripped. The legacy agent removed ``` fences from
    # model output before executing it, which silently accepted a response that
    # had ignored the requested format. With structured output a fence means
    # something is wrong upstream, and that should be visible.
    if parsed.sql is not None and "```" in parsed.sql:
        return ModelInvocation(
            metadata=metadata, error="The model returned a fenced code block instead of a bare statement."
        )

    return ModelInvocation(metadata=metadata, value=parsed)


def write_answer(
    model: BaseChatModel,
    config: ModelConfiguration,
    *,
    purpose: str,
    system: str,
    user: str,
) -> ModelInvocation[str]:
    """Ask the model to describe an already-retrieved result in plain language."""
    stopwatch = Stopwatch()
    response = model.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    metadata = _metadata(config, purpose, stopwatch, response)

    text = _message_text(response).strip()
    if not text:
        return ModelInvocation(metadata=metadata, error="The model returned an empty answer.")
    return ModelInvocation(metadata=metadata, value=text)


def _failed[T](config: ModelConfiguration, purpose: str, stopwatch: Stopwatch, reason: str) -> ModelInvocation[T]:
    """Report a call that produced no usable value, with what it still cost."""
    return ModelInvocation(metadata=_metadata(config, purpose, stopwatch, None), error=reason)


def _metadata(config: ModelConfiguration, purpose: str, stopwatch: Stopwatch, raw: object) -> ModelCallMetadata:
    """Record what the call cost, taking usage only where the provider reported it."""
    usage = getattr(raw, "usage_metadata", None)
    tokens: dict[str, object] = usage if isinstance(usage, dict) else {}
    return ModelCallMetadata(
        purpose=purpose,
        deployment=config.deployment,
        latency_ms=stopwatch.stop(),
        input_tokens=_token_count(tokens.get("input_tokens")),
        output_tokens=_token_count(tokens.get("output_tokens")),
        total_tokens=_token_count(tokens.get("total_tokens")),
    )


def _token_count(value: object) -> int | None:
    """Return a reported token count, or ``None`` when the provider omitted it."""
    return value if isinstance(value, int) else None


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
