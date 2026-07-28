"""Pre-provisioning gate.

Runs before ``azd provision``. Verifies the workstation, the Azure sign-in, and
that the configured model can actually be deployed, then prints the topology and
the billable resources so nothing is created without being shown first.

This is an adapter. Every check it performs lives in
``enterprise_agents_on_foundry.infrastructure``; the script only decides what to
print and what to exit with.

Usage:
    uv run python scripts/preprovision_check.py
"""

from __future__ import annotations

import sys

from enterprise_agents_on_foundry.cli.console import EXIT_FAILED, EXIT_OK, EXIT_UNAVAILABLE, bullets, section
from enterprise_agents_on_foundry.config.settings import load_settings
from enterprise_agents_on_foundry.errors import AzureEnvironmentError, ConfigurationError
from enterprise_agents_on_foundry.infrastructure.environment import (
    check_all_prerequisites,
    check_subscription,
    get_signed_in_principal_id,
)
from enterprise_agents_on_foundry.infrastructure.model_catalog import check_model_availability

BILLABLE_RESOURCES = (
    ("Microsoft Foundry resource", "S0", "Charged per token on the model deployment, not hourly."),
    ("Model deployment", "GlobalStandard", "Pay per 1K tokens. No standing charge when idle."),
    ("Azure SQL database", "GP_S_Gen5 serverless", "Billed per vCore-second. Auto-pauses after 60 minutes idle."),
    ("Log Analytics workspace", "PerGB2018", "Billed per GB ingested. 30 day retention."),
    ("Application Insights", "workspace-based", "Ingestion billed through the Log Analytics workspace."),
    ("Key Vault", "standard", "Billed per 10,000 operations. Effectively zero when unused."),
    ("Managed identity", "n/a", "No charge."),
)


def main(argv: list[str] | None = None) -> int:
    """Run the gate and return a process exit code."""
    if argv:
        print(f"preprovision_check.py takes no arguments; received: {' '.join(argv)}")
        return EXIT_UNAVAILABLE

    try:
        settings = load_settings()
    except ConfigurationError as error:
        print(error)
        return EXIT_UNAVAILABLE

    failures: list[str] = []

    section("1. Workstation prerequisites")
    report = check_all_prerequisites()
    for name, status, version in report.as_rows():
        print(f"  {name:<8} {status:<9} {version}")
    if not report.ok:
        failures.append(f"Missing required tools: {', '.join(report.missing_required)}")

    section("2. Azure sign-in")
    principal_name = ""
    try:
        check = check_subscription(settings.azure_subscription_id)
        print(f"  {check.message}")
        principal_name = check.account.user_name
        if not check.matches:
            failures.append("Signed-in subscription does not match AZURE_SUBSCRIPTION_ID.")
        print(f"  Principal object id: {get_signed_in_principal_id()}")
    except AzureEnvironmentError as error:
        print(f"  {error}")
        failures.append(f"Azure context unavailable: {error}")

    section("3. Model availability")
    model_name = settings.azure_model_name or ""
    model_version = settings.azure_model_version or ""
    if not model_name or not model_version:
        print("  Skipped: the model name or version is not configured.")
        failures.append("AZURE_MODEL_NAME and AZURE_MODEL_VERSION must both be set before provisioning.")
    else:
        print(
            f"  Requested: {model_name} {model_version} on {settings.azure_model_sku_name}, "
            f"{settings.azure_model_capacity}K TPM in {settings.azure_location}"
        )
        try:
            availability = check_model_availability(
                location=settings.azure_location,
                model_name=model_name,
                model_version=model_version,
                sku_name=settings.azure_model_sku_name,
                capacity=settings.azure_model_capacity,
            )
            reason = availability.failure_reason()
            if reason:
                print(f"  BLOCKED: {reason}")
                failures.append(reason)
            else:
                print(f"  Available. Remaining quota: {availability.remaining_quota:g}K TPM.")
        except AzureEnvironmentError as error:
            print(f"  Could not query the model catalog: {error}")
            failures.append(f"Model catalog unavailable: {error}")

    section("4. Resources that will be created")
    for resource, sku, note in BILLABLE_RESOURCES:
        print(f"  {resource:<28} {sku:<22} {note}")
    print("\n  Not provisioned yet: Azure Container Registry, Azure AI Search,")
    print("  Foundry IQ, long-term memory, private networking, external hosting.")

    section("5. Target")
    print(f"  Subscription : {settings.azure_subscription_id or '(from az account show)'}")
    print(f"  Region       : {settings.azure_location}")
    print(f"  Environment  : {settings.environment_name}")
    print(f"  Dataset      : {settings.dataset_variant}")
    print(f"  SQL admin    : {principal_name or '(signed-in user)'}")

    if failures:
        section("Result: BLOCKED")
        bullets(failures)
        return EXIT_FAILED

    section("Result: READY")
    print("  Run: azd provision")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
