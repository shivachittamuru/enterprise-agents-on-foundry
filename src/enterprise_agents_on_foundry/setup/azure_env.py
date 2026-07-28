"""Azure subscription context and azd environment outputs.

Two distinct questions are answered here: which subscription am I signed in to,
and what did the last provisioning run produce. Keeping them separate matters
because a developer can be signed in without having provisioned anything.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Final

_COMMAND_TIMEOUT_SECONDS: Final = 120


class AzureContextError(RuntimeError):
    """Raised when Azure context cannot be determined."""


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


def _run(command: list[str]) -> str:
    """Run a command with a fixed argument list and return stdout."""
    executable = shutil.which(command[0])
    if executable is None:
        raise AzureContextError(f"'{command[0]}' was not found on PATH.")

    try:
        completed = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            [executable, *command[1:]],
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.SubprocessError, OSError) as error:
        raise AzureContextError(f"Failed to run '{' '.join(command)}': {error}") from error

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise AzureContextError(f"'{' '.join(command)}' failed: {detail}")

    return completed.stdout


def get_signed_in_account() -> AzureAccount:
    """Return the account reported by ``az account show``."""
    raw = _run(["az", "account", "show", "--output", "json"])
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise AzureContextError("Could not parse the output of 'az account show'.") from error

    user = payload.get("user") or {}
    return AzureAccount(
        subscription_id=str(payload.get("id", "")),
        subscription_name=str(payload.get("name", "")),
        tenant_id=str(payload.get("tenantId", "")),
        user_name=str(user.get("name", "")),
        user_type=str(user.get("type", "")),
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
    raw = _run(["az", "ad", "signed-in-user", "show", "--query", "id", "--output", "tsv"])
    principal_id = raw.strip()
    if not principal_id:
        raise AzureContextError("Could not determine the signed-in principal object id.")
    return principal_id


def load_azd_env_values(environment_name: str | None = None) -> dict[str, str]:
    """Return the outputs of the current azd environment.

    An empty mapping is returned when azd is installed but no environment has
    been provisioned, because that is an expected state before the first run.
    """
    command = ["azd", "env", "get-values", "--output", "json"]
    if environment_name:
        command.extend(["--environment", environment_name])

    try:
        raw = _run(command)
    except AzureContextError:
        return {}

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items() if value is not None}
