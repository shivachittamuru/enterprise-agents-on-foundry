"""Static contracts the Bicep templates must keep.

These are text assertions, not deployments. They exist because the properties
being checked are security decisions that would be easy to weaken accidentally
during a later refactor, and a compile check would not catch any of them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

MODULES = (
    "foundry",
    "identity",
    "keyvault",
    "monitoring",
    "platform",
    "rbac",
    "registry",
    "sql",
)


@pytest.fixture(scope="module")
def infra(repo_root: Path) -> Path:
    return repo_root / "infra"


@pytest.mark.parametrize("module", MODULES)
def test_module_exists(infra: Path, module: str) -> None:
    assert (infra / "modules" / f"{module}.bicep").is_file()


def test_main_is_subscription_scoped(infra: Path) -> None:
    """The template creates its own resource group, so it cannot be group scoped."""
    assert "targetScope = 'subscription'" in (infra / "main.bicep").read_text(encoding="utf-8")


def test_sql_uses_entra_only_authentication(infra: Path) -> None:
    content = (infra / "modules" / "sql.bicep").read_text(encoding="utf-8")

    assert "azureADOnlyAuthentication: true" in content
    assert "administratorType: 'ActiveDirectory'" in content


def test_sql_declares_no_password_parameter(infra: Path) -> None:
    """A SQL admin password must be impossible to supply, not merely optional."""
    lines = (infra / "modules" / "sql.bicep").read_text(encoding="utf-8").splitlines()
    code = "\n".join(line for line in lines if not line.strip().startswith("//")).lower()

    assert "administratorlogin" not in code
    assert "administratorloginpassword" not in code
    assert "password" not in code


def test_sql_enforces_modern_tls(infra: Path) -> None:
    assert "minimalTlsVersion: '1.2'" in (infra / "modules" / "sql.bicep").read_text(encoding="utf-8")


def test_sql_database_is_serverless_and_auto_pauses(infra: Path) -> None:
    """Auto-pause is what keeps the teaching environment close to zero cost."""
    content = (infra / "modules" / "sql.bicep").read_text(encoding="utf-8")

    assert "GP_S_Gen5" in content
    assert "autoPauseDelay" in content


def test_foundry_disables_local_authentication(infra: Path) -> None:
    """Key-based access was the legacy model. Entra replaces it."""
    assert "disableLocalAuth: true" in (infra / "modules" / "foundry.bicep").read_text(encoding="utf-8")


def test_foundry_enables_project_management(infra: Path) -> None:
    assert "allowProjectManagement: true" in (infra / "modules" / "foundry.bicep").read_text(encoding="utf-8")


def test_key_vault_uses_rbac_not_access_policies(infra: Path) -> None:
    content = (infra / "modules" / "keyvault.bicep").read_text(encoding="utf-8")

    assert "enableRbacAuthorization: true" in content
    assert "accessPolicies" not in content


def test_registry_never_enables_the_admin_user(infra: Path) -> None:
    assert "adminUserEnabled: false" in (infra / "modules" / "registry.bicep").read_text(encoding="utf-8")


def test_rbac_grants_no_sql_data_plane_role(infra: Path) -> None:
    """Database permissions belong in the database, not in a control-plane role."""
    content = (infra / "modules" / "rbac.bicep").read_text(encoding="utf-8")

    assert "Microsoft.Sql" not in content
    assert "db_datareader" not in content


def test_application_insights_connection_string_is_not_an_output(infra: Path) -> None:
    """Deployment outputs are readable by anyone with deployment history access."""
    for name in ("main.bicep", "modules/platform.bicep", "modules/monitoring.bicep"):
        content = (infra / name).read_text(encoding="utf-8")
        outputs = [line for line in content.splitlines() if line.strip().startswith("output ")]
        assert not any("ConnectionString" in line or "connectionString" in line for line in outputs), (
            f"{name} exposes a connection string as an output"
        )


@pytest.mark.parametrize(
    "flag",
    [
        "enableAzureAiSearch",
        "enableContainerRegistry",
        "enableExternalHosting",
        "enableFoundryIq",
        "enableHostedAgent",
        "enableLongTermMemory",
        "enablePrivateNetworking",
    ],
)
def test_optional_capability_defaults_to_false(infra: Path, flag: str) -> None:
    content = (infra / "main.bicep").read_text(encoding="utf-8")
    assert f"param {flag} bool = false" in content, f"{flag} must default to false in v0.1"


def test_parameters_file_contains_no_literal_secrets(infra: Path) -> None:
    content = (infra / "main.parameters.json").read_text(encoding="utf-8").lower()

    assert "password" not in content
    assert "secret" not in content


def test_outputs_match_env_example_keys(repo_root: Path, infra: Path) -> None:
    """azd copies outputs into the environment, so the names have to line up."""
    example_keys = {
        line.split("=", 1)[0].strip()
        for line in (repo_root / ".env.example").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#") and "=" in line
    }

    output_names = {
        line.split()[1]
        for line in (infra / "main.bicep").read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("output ")
    }

    unknown = {name for name in output_names if name.isupper()} - example_keys
    assert unknown == set(), f"outputs with no matching .env.example key: {sorted(unknown)}"
