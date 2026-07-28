"""``eaof-verify``: report whether this workstation and Azure environment are ready.

The command owns presentation and the exit code. Every fact it prints comes from
:mod:`enterprise_agents_on_foundry.infrastructure`, so the notebook, the azd
pre-provision hook, and this command cannot disagree about what "ready" means.

Exit codes:

* ``0`` everything required is present and consistent
* ``1`` a check reported a problem
* ``2`` configuration could not be loaded at all
"""

from __future__ import annotations

import sys

from enterprise_agents_on_foundry.cli.console import EXIT_FAILED, EXIT_OK, EXIT_UNAVAILABLE, bullets, section
from enterprise_agents_on_foundry.config.settings import Settings, load_settings
from enterprise_agents_on_foundry.errors import ConfigurationError
from enterprise_agents_on_foundry.infrastructure.azd_outputs import load_azd_outputs
from enterprise_agents_on_foundry.infrastructure.environment import EnvironmentHealth, report_environment_health


def _print_configuration(settings: Settings) -> None:
    section("Configuration")
    print(f"  Project     : {settings.project_name}")
    print(f"  Environment : {settings.environment_name}")
    print(f"  Dataset     : {settings.dataset_variant}")
    print(f"  Region      : {settings.azure_location}")
    print(f"  Resource grp: {settings.azure_resource_group or '(not set)'}")
    print(f"  Model       : {settings.azure_model_name or '(not set)'} on {settings.azure_model_sku_name}")


def _print_health(health: EnvironmentHealth) -> None:
    section("Workstation prerequisites")
    for name, status, version in health.prerequisites.as_rows():
        print(f"  {name:<8} {status:<9} {version}")

    section("Azure sign-in")
    print(f"  {health.subscription.message if health.subscription else 'Not signed in.'}")

    section("Deployment")
    print(f"  {health.resource_group.message}")


def main(argv: list[str] | None = None) -> int:
    """Run every environment check and return a process exit code."""
    if argv:
        print(f"eaof-verify takes no arguments; received: {' '.join(argv)}")
        return EXIT_UNAVAILABLE

    try:
        settings = load_settings()
    except ConfigurationError as error:
        print(error)
        return EXIT_UNAVAILABLE

    _print_configuration(settings)

    outputs = load_azd_outputs()
    health = report_environment_health(settings, outputs.resource_group)
    _print_health(health)

    if not health.ok:
        section("Result: NOT READY")
        bullets(health.problems)
        return EXIT_FAILED

    section("Result: READY")
    print("  Run 'uv run eaof-db-info' to inspect the database.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
