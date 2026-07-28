"""Setup and validation helpers used by the v0.1 bootstrap notebook and scripts.

Nothing in this package talks to a language model or builds an agent. The scope is
deliberately limited to verifying that a developer workstation and an Azure
subscription are ready for the rest of the curriculum.
"""

from enterprise_agents_on_foundry.setup.azure_env import (
    AzureAccount,
    AzureContextError,
    SubscriptionCheck,
    check_subscription,
    get_signed_in_account,
    get_signed_in_principal_id,
    load_azd_env_values,
)
from enterprise_agents_on_foundry.setup.config import (
    DEFAULT_DATASET_VARIANT,
    DatasetVariant,
    Settings,
    get_settings,
    load_settings,
    repository_root,
)
from enterprise_agents_on_foundry.setup.database import (
    DatabaseTarget,
    DatabaseTargetError,
    ReadOnlySqlError,
    assert_read_only_sql,
    is_read_only_sql,
    require_bootstrap_allowed,
    resolve_database_target,
)
from enterprise_agents_on_foundry.setup.measurements import (
    Measurement,
    MeasurementSet,
    time_operation,
)
from enterprise_agents_on_foundry.setup.model_catalog import (
    CatalogModel,
    ModelAvailability,
    ModelCatalogError,
    check_model_availability,
    list_catalog_models,
)
from enterprise_agents_on_foundry.setup.prereqs import (
    PrerequisiteReport,
    ToolCheck,
    check_all_prerequisites,
)

__all__ = [
    "DEFAULT_DATASET_VARIANT",
    "AzureAccount",
    "AzureContextError",
    "CatalogModel",
    "DatabaseTarget",
    "DatabaseTargetError",
    "DatasetVariant",
    "Measurement",
    "MeasurementSet",
    "ModelAvailability",
    "ModelCatalogError",
    "PrerequisiteReport",
    "ReadOnlySqlError",
    "Settings",
    "SubscriptionCheck",
    "ToolCheck",
    "assert_read_only_sql",
    "check_all_prerequisites",
    "check_model_availability",
    "check_subscription",
    "get_settings",
    "get_signed_in_account",
    "get_signed_in_principal_id",
    "is_read_only_sql",
    "list_catalog_models",
    "load_azd_env_values",
    "load_settings",
    "repository_root",
    "require_bootstrap_allowed",
    "resolve_database_target",
    "time_operation",
]
