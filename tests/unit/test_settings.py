"""Configuration loading, dataset constraints, and the bootstrap safety switch."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from enterprise_agents_on_foundry.setup.config import (
    DEFAULT_DATASET_VARIANT,
    DatasetVariant,
    Settings,
    SqlAuthentication,
    load_settings,
    repository_root,
)
from enterprise_agents_on_foundry.setup.database import require_bootstrap_allowed


def test_default_dataset_is_adventureworks_lt() -> None:
    assert DEFAULT_DATASET_VARIANT is DatasetVariant.ADVENTUREWORKS_LT
    assert DEFAULT_DATASET_VARIANT == "adventureworks-lt"


def test_settings_default_to_adventureworks_lt(clean_env: None, empty_env_file: Path) -> None:
    settings = load_settings(env_file=empty_env_file)
    assert settings.dataset_variant is DatasetVariant.ADVENTUREWORKS_LT
    assert settings.azure_sql_database_name == "AdventureWorksLT"


def test_dataset_variant_rejects_unknown_values() -> None:
    with pytest.raises(ValidationError):
        Settings(dataset_variant="chinook")  # type: ignore[arg-type]


def test_dataset_variant_accepts_mixed_case_and_whitespace() -> None:
    settings = Settings(dataset_variant="  AdventureWorks-LT  ")  # type: ignore[arg-type]
    assert settings.dataset_variant is DatasetVariant.ADVENTUREWORKS_LT


def test_authentication_is_entra_only(clean_env: None, empty_env_file: Path) -> None:
    settings = load_settings(env_file=empty_env_file)
    assert settings.azure_sql_authentication is SqlAuthentication.ENTRA
    assert [member.value for member in SqlAuthentication] == ["entra"]


def test_settings_have_no_password_field() -> None:
    """Entra-only means there is nowhere for a password to be configured."""
    suspicious = [name for name in Settings.model_fields if any(word in name for word in ("password", "secret", "key"))]
    assert suspicious == []


def test_settings_are_frozen(clean_env: None, empty_env_file: Path) -> None:
    settings = load_settings(env_file=empty_env_file)
    with pytest.raises(ValidationError):
        settings.azure_location = "eastus"  # type: ignore[misc]


def test_bootstrap_is_disabled_by_default(clean_env: None, empty_env_file: Path) -> None:
    settings = load_settings(env_file=empty_env_file)
    assert settings.allow_database_bootstrap is False
    with pytest.raises(PermissionError, match="ALLOW_DATABASE_BOOTSTRAP"):
        require_bootstrap_allowed(settings)


def test_bootstrap_allowed_when_explicitly_enabled() -> None:
    settings = Settings(allow_database_bootstrap=True)
    require_bootstrap_allowed(settings)


def test_env_file_does_not_mutate_process_environment(clean_env: None, tmp_path: Path) -> None:
    """Legacy code called load_dotenv(override=True). This must not."""
    env_file = tmp_path / ".env"
    env_file.write_text("AZURE_LOCATION=swedencentral\n", encoding="utf-8")

    settings = load_settings(env_file=env_file)

    assert settings.azure_location == "swedencentral"
    assert "AZURE_LOCATION" not in os.environ


def test_process_environment_wins_over_env_file(
    clean_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("AZURE_LOCATION=swedencentral\n", encoding="utf-8")
    monkeypatch.setenv("AZURE_LOCATION", "westus3")

    assert load_settings(env_file=env_file).azure_location == "westus3"


def test_env_file_placeholder_is_treated_as_absent(clean_env: None, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("AZURE_SUBSCRIPTION_ID=\nAZURE_RESOURCE_GROUP=   \n", encoding="utf-8")

    settings = load_settings(env_file=env_file)

    assert settings.azure_subscription_id is None
    assert settings.azure_resource_group is None


def test_quoted_env_values_are_unwrapped(clean_env: None, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('AZURE_RESOURCE_GROUP="rg-quoted"\n', encoding="utf-8")

    assert load_settings(env_file=env_file).azure_resource_group == "rg-quoted"


def test_unprovisioned_settings_report_missing_outputs(clean_env: None, empty_env_file: Path) -> None:
    settings = load_settings(env_file=empty_env_file)

    assert settings.is_provisioned is False
    assert settings.missing_provisioning_outputs() == [
        "AZURE_FOUNDRY_PROJECT_ENDPOINT",
        "AZURE_RESOURCE_GROUP",
        "AZURE_SQL_SERVER_FQDN",
        "AZURE_SUBSCRIPTION_ID",
    ]


def test_provisioned_settings_report_nothing_missing() -> None:
    settings = Settings(
        azure_subscription_id="1fad602f-d06f-46af-8f70-78a2c2c53b24",
        azure_resource_group="rg-demo",
        azure_sql_server_fqdn="sql-demo.database.windows.net",
        azure_foundry_project_endpoint="https://aif-demo.services.ai.azure.com/api/projects/proj-demo",
    )

    assert settings.is_provisioned is True
    assert settings.missing_provisioning_outputs() == []


def test_feature_flags_default_to_disabled(clean_env: None, empty_env_file: Path) -> None:
    settings = load_settings(env_file=empty_env_file)
    flags = {name: getattr(settings, name) for name in Settings.model_fields if name.startswith("enable_")}

    assert flags, "expected at least one feature flag"
    assert not any(flags.values()), f"v0.1 must ship with every optional capability off: {flags}"


def test_repository_root_is_anchored_on_the_package_not_the_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    assert (repository_root() / "pyproject.toml").is_file()


def test_query_limits_are_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings(database_max_result_rows=0)
    with pytest.raises(ValidationError):
        Settings(database_connect_timeout_seconds=0)
