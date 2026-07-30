"""The single runtime configuration boundary.

Every value the project reads from the environment is declared here as a typed
field. No module outside this one calls ``os.getenv``, so a configuration key
cannot exist without being visible in :class:`Settings`.

Configuration is read from environment variables, optionally seeded from a local
``.env`` file. Secrets are never written back to disk by this module, and the
Azure SQL contract is Microsoft Entra authentication only: there is deliberately
no field for a SQL administrator password.
"""

from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from enterprise_agents_on_foundry.errors import ConfigurationError


class DatasetVariant(StrEnum):
    """Datasets this repository is allowed to target.

    Full AdventureWorks OLTP is explicitly out of scope, so the enumeration has
    exactly one member. Keeping it an enumeration rather than a bare string makes
    the eventual second dataset an additive change.
    """

    ADVENTUREWORKS_LT = "adventureworks-lt"


DEFAULT_DATASET_VARIANT: DatasetVariant = DatasetVariant.ADVENTUREWORKS_LT
"""The dataset used when ``DATASET_VARIANT`` is not set."""


class SqlAuthentication(StrEnum):
    """Supported Azure SQL authentication modes."""

    ENTRA = "entra"


def repository_root() -> Path:
    """Return the repository root, resolved from this file rather than the CWD.

    The legacy backend resolved paths relative to the working directory, which
    broke whenever a script was launched from a different folder. Anchoring on
    ``__file__`` removes that class of failure.
    """
    return Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Validated view of the environment for setup, validation, and database work."""

    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        frozen=True,
    )

    project_name: str = Field(default="enterprise-agents-on-foundry")
    environment_name: str = Field(default="dev")
    dataset_variant: DatasetVariant = Field(default=DEFAULT_DATASET_VARIANT)

    azure_subscription_id: str | None = Field(default=None)
    azure_tenant_id: str | None = Field(default=None)
    azure_location: str = Field(default="westus3")
    azure_resource_group: str | None = Field(default=None)

    azure_foundry_resource_name: str | None = Field(default=None)
    azure_foundry_project_name: str | None = Field(default=None)
    azure_foundry_project_endpoint: str | None = Field(default=None)
    # The project endpoint addresses the Foundry project and is what agent and
    # evaluation services use. The account endpoint addresses the underlying
    # Azure OpenAI surface and is what a chat completions client needs. They are
    # different hosts, so both are kept rather than deriving one from the other.
    azure_foundry_account_endpoint: str | None = Field(default=None)

    azure_model_deployment_name: str | None = Field(default=None)
    azure_model_name: str | None = Field(default=None)
    azure_model_version: str | None = Field(default=None)
    # v0.1 read these two through os.environ.get inside a script, so they were
    # configuration in practice while being invisible to the type checker.
    azure_model_sku_name: str = Field(default="GlobalStandard")
    azure_model_capacity: int = Field(default=10, ge=1, le=10_000)
    azure_openai_api_version: str = Field(default="2025-04-01-preview", min_length=1)

    azure_sql_server_name: str | None = Field(default=None)
    azure_sql_server_fqdn: str | None = Field(default=None)
    azure_sql_database_name: str = Field(default="AdventureWorksLT")
    azure_sql_authentication: SqlAuthentication = Field(default=SqlAuthentication.ENTRA)

    database_connect_timeout_seconds: int = Field(default=30, ge=1, le=300)
    database_query_timeout_seconds: int = Field(default=30, ge=1, le=300)
    database_max_result_rows: int = Field(default=500, ge=1, le=10_000)

    allow_database_bootstrap: bool = Field(default=False)

    applicationinsights_connection_string: str | None = Field(default=None)
    azure_log_analytics_workspace_name: str | None = Field(default=None)

    enable_azure_ai_search: bool = Field(default=False)
    enable_foundry_iq: bool = Field(default=False)
    enable_long_term_memory: bool = Field(default=False)
    enable_private_networking: bool = Field(default=False)
    enable_hosted_agent: bool = Field(default=False)
    enable_container_registry: bool = Field(default=False)
    enable_external_hosting: bool = Field(default=False)

    @field_validator("dataset_variant", mode="before")
    @classmethod
    def _normalise_dataset_variant(cls, value: object) -> object:
        """Accept mixed case and stray whitespace, reject unknown datasets."""
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator(
        "azure_subscription_id",
        "azure_tenant_id",
        "azure_resource_group",
        "azure_foundry_resource_name",
        "azure_foundry_project_name",
        "azure_foundry_project_endpoint",
        "azure_foundry_account_endpoint",
        "azure_model_deployment_name",
        "azure_model_name",
        "azure_model_version",
        "azure_sql_server_name",
        "azure_sql_server_fqdn",
        "applicationinsights_connection_string",
        "azure_log_analytics_workspace_name",
        mode="before",
    )
    @classmethod
    def _empty_string_is_none(cls, value: object) -> object:
        """Treat an unfilled ``.env`` placeholder as absent rather than blank."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def is_provisioned(self) -> bool:
        """True when the azd or Bicep outputs needed for validation are present."""
        return not self.missing_provisioning_outputs()

    def missing_provisioning_outputs(self) -> list[str]:
        """Name the settings that must be populated before validation can run."""
        required = {
            "AZURE_SUBSCRIPTION_ID": self.azure_subscription_id,
            "AZURE_RESOURCE_GROUP": self.azure_resource_group,
            "AZURE_SQL_SERVER_FQDN": self.azure_sql_server_fqdn,
            "AZURE_FOUNDRY_PROJECT_ENDPOINT": self.azure_foundry_project_endpoint,
        }
        return sorted(name for name, value in required.items() if not value)


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse a ``.env`` file without mutating ``os.environ``.

    A deliberately small parser is used instead of ``load_dotenv(override=True)``.
    The legacy backend overrode real process environment variables from whatever
    ``.env`` file happened to be found first, which made CI and local runs differ.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def _describe(error: ValidationError) -> str:
    """Turn a pydantic error into a message naming environment variables.

    A raw ``ValidationError`` names lower-case Python fields, which do not match
    what a developer wrote in ``.env``. Only the field name and the reason are
    reproduced, never the supplied value, so an invalid secret cannot leak.
    """
    lines = []
    for detail in error.errors():
        location = ".".join(str(part) for part in detail["loc"]) or "settings"
        lines.append(f"  {location.upper()}: {detail['msg']}")
    return "\n".join(lines)


def load_settings(env_file: Path | None = None) -> Settings:
    """Build :class:`Settings` from a ``.env`` file layered under the real environment.

    Process environment variables win over file values, which is the opposite of
    the legacy behaviour and the safer default for CI.

    Raises:
        ConfigurationError: when a value is missing or fails validation.
    """
    path = env_file if env_file is not None else repository_root() / ".env"
    file_values = {key.lower(): value for key, value in _read_env_file(path).items()}
    process_values = {key.lower(): value for key, value in os.environ.items()}
    merged = file_values | process_values

    try:
        return Settings(**merged)  # type: ignore[arg-type]
    except ValidationError as error:
        raise ConfigurationError(
            f"Configuration is not valid. Check {path.name} and the process environment:\n{_describe(error)}"
        ) from error


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return process-wide settings, loaded once."""
    return load_settings()
