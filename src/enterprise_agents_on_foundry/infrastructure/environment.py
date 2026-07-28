"""Workstation prerequisites, Azure sign-in, and overall environment health.

Two distinct questions are answered here: is this workstation able to run the
curriculum, and does the Azure context it is pointed at match what configuration
says. Keeping them together is deliberate, because "is my environment ready" is
one question a developer asks once, not three questions asked separately.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Final

from enterprise_agents_on_foundry.config.settings import Settings
from enterprise_agents_on_foundry.errors import AzureEnvironmentError
from enterprise_agents_on_foundry.infrastructure.commands import run_command, run_json_command

MINIMUM_PYTHON: Final[tuple[int, int]] = (3, 12)

_RESOURCE_GROUP_PATTERN: Final = re.compile(r"^[a-zA-Z0-9._()-]{1,89}[a-zA-Z0-9_()-]$")

__all__ = [
    "MINIMUM_PYTHON",
    "AzureAccount",
    "EnvironmentHealth",
    "PrerequisiteReport",
    "ResourceGroupCheck",
    "SubscriptionCheck",
    "ToolCheck",
    "check_all_prerequisites",
    "check_bicep",
    "check_expected_resource_group",
    "check_python",
    "check_subscription",
    "check_tool",
    "get_signed_in_account",
    "get_signed_in_principal_id",
    "report_environment_health",
]


@dataclass(frozen=True, slots=True)
class ToolCheck:
    """The outcome of checking one command-line tool."""

    name: str
    required: bool
    found: bool
    version: str | None = None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        """True when the tool is present, or absent but optional."""
        return self.found or not self.required

    @property
    def status(self) -> str:
        """A short status token suitable for a table cell."""
        if self.found:
            return "ok"
        return "missing" if self.required else "optional"


@dataclass(frozen=True, slots=True)
class PrerequisiteReport:
    """The aggregate result of all prerequisite checks."""

    checks: tuple[ToolCheck, ...] = ()

    @property
    def ok(self) -> bool:
        """True when every required tool was found."""
        return all(check.ok for check in self.checks)

    @property
    def missing_required(self) -> tuple[str, ...]:
        """Names of required tools that were not found."""
        return tuple(check.name for check in self.checks if check.required and not check.found)

    def as_rows(self) -> list[tuple[str, str, str]]:
        """Return ``(name, status, version)`` rows for display."""
        return [(check.name, check.status, check.version or "-") for check in self.checks]


@dataclass(frozen=True, slots=True)
class AzureAccount:
    """The currently signed-in Azure CLI account."""

    subscription_id: str
    subscription_name: str
    tenant_id: str
    user_name: str
    user_type: str

    @property
    def display(self) -> str:
        """A short description suitable for a confirmation prompt."""
        return f"{self.subscription_name} ({self.subscription_id}) as {self.user_name}"


@dataclass(frozen=True, slots=True)
class SubscriptionCheck:
    """Comparison between the signed-in subscription and the configured one."""

    account: AzureAccount
    expected_subscription_id: str | None
    matches: bool

    @property
    def message(self) -> str:
        """Explain the comparison result in one sentence."""
        if self.expected_subscription_id is None:
            return f"Signed in to {self.account.display}. AZURE_SUBSCRIPTION_ID is not set, so nothing was compared."
        if self.matches:
            return f"Signed in to the expected subscription: {self.account.display}."
        return (
            f"Signed in to {self.account.subscription_id} but AZURE_SUBSCRIPTION_ID "
            f"is {self.expected_subscription_id}. Run 'az account set --subscription "
            f"{self.expected_subscription_id}' before provisioning."
        )


@dataclass(frozen=True, slots=True)
class ResourceGroupCheck:
    """Comparison between the configured resource group and the deployed one."""

    expected: str | None
    actual: str | None
    matches: bool
    reason: str | None = None

    @property
    def message(self) -> str:
        """Explain the comparison result in one sentence."""
        if self.reason:
            return self.reason
        return f"Resource group '{self.expected}' matches the deployment outputs."


@dataclass(frozen=True, slots=True)
class EnvironmentHealth:
    """Everything ``eaof-verify`` needs to decide on an exit code."""

    prerequisites: PrerequisiteReport
    subscription: SubscriptionCheck | None
    resource_group: ResourceGroupCheck
    missing_settings: tuple[str, ...]
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """True when nothing blocking was found."""
        return not self.problems


def _first_line(text: str) -> str:
    """Return the first non-empty line, trimmed."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def check_python() -> ToolCheck:
    """Check that the running interpreter meets the minimum version."""
    version = ".".join(str(part) for part in sys.version_info[:3])
    meets_minimum = sys.version_info[:2] >= MINIMUM_PYTHON
    minimum = ".".join(str(part) for part in MINIMUM_PYTHON)
    return ToolCheck(
        name="python",
        required=True,
        found=meets_minimum,
        version=version,
        detail=None if meets_minimum else f"Python {minimum} or newer is required.",
    )


def check_tool(name: str, arguments: list[str], *, required: bool = True) -> ToolCheck:
    """Check a single tool by running its version command."""
    result = run_command([name, *arguments], timeout_seconds=60)
    if not result.ok:
        return ToolCheck(name=name, required=required, found=False, detail=result.detail)
    version = _first_line(result.stdout) or _first_line(result.stderr)
    return ToolCheck(name=name, required=required, found=True, version=version)


def check_bicep() -> ToolCheck:
    """Check the Bicep CLI bundled with the Azure CLI.

    Bicep is normally installed as an Azure CLI extension rather than a
    standalone binary on PATH, so it is checked through ``az bicep version``.
    """
    check = check_tool("az", ["bicep", "version"])
    return ToolCheck(
        name="bicep",
        required=True,
        found=check.found,
        version=check.version,
        detail=check.detail,
    )


def check_all_prerequisites() -> PrerequisiteReport:
    """Check every tool the curriculum depends on."""
    checks = (
        check_python(),
        check_tool("git", ["--version"]),
        check_tool("uv", ["--version"]),
        # 'az version --query azure-cli' fails, because JMESPath reads the hyphen
        # as subtraction. 'az --version' reports the same value on its first line.
        check_tool("az", ["--version"]),
        check_tool("azd", ["version"]),
        check_bicep(),
    )
    return PrerequisiteReport(checks=checks)


def get_signed_in_account() -> AzureAccount:
    """Return the account reported by ``az account show``.

    Raises:
        AzureEnvironmentError: when the Azure CLI is missing or not signed in.
    """
    payload = run_json_command(["az", "account", "show", "--output", "json"])
    if not isinstance(payload, dict):
        raise AzureEnvironmentError("'az account show' did not return an account object.")

    user = payload.get("user")
    user_fields = user if isinstance(user, dict) else {}
    return AzureAccount(
        subscription_id=str(payload.get("id", "")),
        subscription_name=str(payload.get("name", "")),
        tenant_id=str(payload.get("tenantId", "")),
        user_name=str(user_fields.get("name", "")),
        user_type=str(user_fields.get("type", "")),
    )


def check_subscription(expected_subscription_id: str | None) -> SubscriptionCheck:
    """Compare the signed-in subscription against the configured one."""
    account = get_signed_in_account()
    matches = expected_subscription_id is None or account.subscription_id == expected_subscription_id
    return SubscriptionCheck(
        account=account,
        expected_subscription_id=expected_subscription_id,
        matches=matches,
    )


def get_signed_in_principal_id() -> str:
    """Return the object id of the signed-in principal.

    This value is passed to Bicep so the deploying user becomes the Microsoft
    Entra administrator of the Azure SQL logical server.
    """
    result = run_command(["az", "ad", "signed-in-user", "show", "--query", "id", "--output", "tsv"])
    if not result.ok:
        raise AzureEnvironmentError(f"'{result.command}' failed: {result.detail}")
    principal_id = result.stdout.strip()
    if not principal_id:
        raise AzureEnvironmentError("Could not determine the signed-in principal object id.")
    return principal_id


def check_expected_resource_group(expected: str | None, actual: str | None) -> ResourceGroupCheck:
    """Compare the configured resource group against the deployed one.

    This is a pure comparison so it can be exercised without an Azure round trip.
    A mismatch is treated as blocking rather than as a warning: silently running
    validation against a different resource group is how the wrong environment
    gets modified.
    """
    if not expected:
        return ResourceGroupCheck(
            expected=None,
            actual=actual,
            matches=False,
            reason="AZURE_RESOURCE_GROUP is not set. Run 'azd provision', then 'uv run eaof-verify' again.",
        )
    if not _RESOURCE_GROUP_PATTERN.match(expected):
        return ResourceGroupCheck(
            expected=expected,
            actual=actual,
            matches=False,
            reason=f"'{expected}' is not a valid Azure resource group name.",
        )
    if actual is None:
        return ResourceGroupCheck(
            expected=expected,
            actual=None,
            matches=False,
            reason=(
                f"Configuration expects resource group '{expected}', but no azd deployment "
                f"output was found to compare it against."
            ),
        )
    if expected.casefold() != actual.casefold():
        return ResourceGroupCheck(
            expected=expected,
            actual=actual,
            matches=False,
            reason=(
                f"Configuration expects resource group '{expected}' but the azd environment "
                f"deployed '{actual}'. Refusing to guess which one is correct."
            ),
        )
    return ResourceGroupCheck(expected=expected, actual=actual, matches=True)


def report_environment_health(settings: Settings, deployed_resource_group: str | None) -> EnvironmentHealth:
    """Collect every environment check into one result.

    Azure sign-in failures are recorded rather than raised, because a report that
    stops at the first problem forces a developer to fix issues one round trip at
    a time.
    """
    problems: list[str] = []

    prerequisites = check_all_prerequisites()
    if not prerequisites.ok:
        problems.append(f"Missing required tools: {', '.join(prerequisites.missing_required)}.")

    subscription: SubscriptionCheck | None = None
    try:
        subscription = check_subscription(settings.azure_subscription_id)
        if not subscription.matches:
            problems.append(subscription.message)
    except AzureEnvironmentError as error:
        problems.append(f"Azure context unavailable: {error}")

    resource_group = check_expected_resource_group(settings.azure_resource_group, deployed_resource_group)
    if not resource_group.matches:
        problems.append(resource_group.message)

    missing_settings = tuple(settings.missing_provisioning_outputs())
    if missing_settings:
        problems.append(f"Provisioning outputs are missing: {', '.join(missing_settings)}.")

    return EnvironmentHealth(
        prerequisites=prerequisites,
        subscription=subscription,
        resource_group=resource_group,
        missing_settings=missing_settings,
        problems=tuple(problems),
    )
