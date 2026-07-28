"""Pre-provisioning gate.

Runs before ``azd provision``. Verifies the workstation, the Azure sign-in, and
that the configured model can actually be deployed, then prints the topology and
the billable resources so nothing is created without being shown first.

Usage:
    uv run python scripts/preprovision_check.py
"""

from __future__ import annotations

import os
import sys

from enterprise_agents_on_foundry.setup.azure_env import (
    AzureContextError,
    check_subscription,
    get_signed_in_principal_id,
)
from enterprise_agents_on_foundry.setup.config import load_settings
from enterprise_agents_on_foundry.setup.model_catalog import ModelCatalogError, check_model_availability
from enterprise_agents_on_foundry.setup.prereqs import check_all_prerequisites

BILLABLE_RESOURCES = (
    ("Microsoft Foundry resource", "S0", "Charged per token on the model deployment, not hourly."),
    ("Model deployment", "GlobalStandard", "Pay per 1K tokens. No standing charge when idle."),
    ("Azure SQL database", "GP_S_Gen5 serverless", "Billed per vCore-second. Auto-pauses after 60 minutes idle."),
    ("Log Analytics workspace", "PerGB2018", "Billed per GB ingested. 30 day retention."),
    ("Application Insights", "workspace-based", "Ingestion billed through the Log Analytics workspace."),
    ("Key Vault", "standard", "Billed per 10,000 operations. Effectively zero when unused."),
    ("Managed identity", "n/a", "No charge."),
)


def _print_header(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


def main() -> int:
    settings = load_settings()
    failures: list[str] = []

    _print_header("1. Workstation prerequisites")
    report = check_all_prerequisites()
    for name, status, version in report.as_rows():
        print(f"  {name:<8} {status:<9} {version}")
    if not report.ok:
        failures.append(f"Missing required tools: {', '.join(report.missing_required)}")

    _print_header("2. Azure sign-in")
    principal_id = ""
    principal_name = ""
    try:
        check = check_subscription(settings.azure_subscription_id)
        print(f"  {check.message}")
        principal_name = check.account.user_name
        if not check.matches:
            failures.append("Signed-in subscription does not match AZURE_SUBSCRIPTION_ID.")
        principal_id = get_signed_in_principal_id()
        print(f"  Principal object id: {principal_id}")
    except AzureContextError as error:
        print(f"  {error}")
        failures.append(f"Azure context unavailable: {error}")

    _print_header("3. Model availability")
    model_name = settings.azure_model_name or os.environ.get("AZURE_MODEL_NAME", "gpt-5.4-mini")
    model_version = settings.azure_model_version or os.environ.get("AZURE_MODEL_VERSION", "2026-03-17")
    sku_name = os.environ.get("AZURE_MODEL_SKU_NAME", "GlobalStandard")
    capacity = int(os.environ.get("AZURE_MODEL_CAPACITY", "10"))

    print(f"  Requested: {model_name} {model_version} on {sku_name}, {capacity}K TPM in {settings.azure_location}")
    try:
        availability = check_model_availability(
            location=settings.azure_location,
            model_name=model_name,
            model_version=model_version,
            sku_name=sku_name,
            capacity=capacity,
        )
        reason = availability.failure_reason()
        if reason:
            print(f"  BLOCKED: {reason}")
            failures.append(reason)
        else:
            remaining = (availability.quota_limit or 0) - (availability.quota_used or 0)
            print(f"  Available. Remaining quota: {remaining:g}K TPM.")
    except ModelCatalogError as error:
        print(f"  Could not query the model catalog: {error}")
        failures.append(f"Model catalog unavailable: {error}")

    _print_header("4. Resources that will be created")
    for resource, sku, note in BILLABLE_RESOURCES:
        print(f"  {resource:<28} {sku:<22} {note}")
    print("\n  Not provisioned in v0.1: Azure Container Registry, Azure AI Search,")
    print("  Foundry IQ, long-term memory, private networking, external hosting.")

    _print_header("5. Target")
    print(f"  Subscription : {settings.azure_subscription_id or '(from az account show)'}")
    print(f"  Region       : {settings.azure_location}")
    print(f"  Environment  : {settings.environment_name}")
    print(f"  Dataset      : {settings.dataset_variant}")
    print(f"  SQL admin    : {principal_name or '(signed-in user)'}")

    if failures:
        _print_header("Result: BLOCKED")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    _print_header("Result: READY")
    print("  Run: azd provision")
    return 0


if __name__ == "__main__":
    sys.exit(main())
