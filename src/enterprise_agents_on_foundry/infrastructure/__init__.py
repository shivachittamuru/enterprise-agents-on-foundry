"""Azure environment inspection: tools, sign-in, deployment outputs, model catalog.

Nothing here provisions or modifies Azure. These modules answer questions about
an environment that already exists so that scripts, the CLI, and notebooks can
share one implementation of each answer.
"""

from enterprise_agents_on_foundry.infrastructure.azd_outputs import (
    PROTECTED_KEYS,
    AzdOutputs,
    declared_env_keys,
    load_azd_outputs,
    parse_azd_outputs,
    write_env_file,
)
from enterprise_agents_on_foundry.infrastructure.commands import CommandResult, run_command, run_json_command
from enterprise_agents_on_foundry.infrastructure.environment import (
    AzureAccount,
    EnvironmentHealth,
    PrerequisiteReport,
    ResourceGroupCheck,
    SubscriptionCheck,
    ToolCheck,
    check_all_prerequisites,
    check_expected_resource_group,
    check_subscription,
    get_signed_in_account,
    get_signed_in_principal_id,
    report_environment_health,
)
from enterprise_agents_on_foundry.infrastructure.model_catalog import (
    CatalogModel,
    ModelAvailability,
    check_model_availability,
)

__all__ = [
    "PROTECTED_KEYS",
    "AzdOutputs",
    "AzureAccount",
    "CatalogModel",
    "CommandResult",
    "EnvironmentHealth",
    "ModelAvailability",
    "PrerequisiteReport",
    "ResourceGroupCheck",
    "SubscriptionCheck",
    "ToolCheck",
    "check_all_prerequisites",
    "check_expected_resource_group",
    "check_model_availability",
    "check_subscription",
    "declared_env_keys",
    "get_signed_in_account",
    "get_signed_in_principal_id",
    "load_azd_outputs",
    "parse_azd_outputs",
    "report_environment_health",
    "run_command",
    "run_json_command",
    "write_env_file",
]
