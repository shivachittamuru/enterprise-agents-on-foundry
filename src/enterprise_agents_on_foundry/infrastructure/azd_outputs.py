"""Reading azd deployment outputs and projecting them onto a local ``.env`` file.

Parsing is separated from invocation so the parsing rules can be tested without
azd installed, and so a malformed azd response produces a typed result rather
than a dictionary of unknown shape.

Output values are treated as data, never as code, and are never logged. Only key
names are printed by the callers in this repository.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from enterprise_agents_on_foundry.errors import AzureEnvironmentError
from enterprise_agents_on_foundry.infrastructure.commands import run_command

PROTECTED_KEYS: frozenset[str] = frozenset(
    {
        # A local safety switch, not a deployment output.
        "ALLOW_DATABASE_BOOTSTRAP",
        # Carries an instrumentation key, so it is left to the developer.
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
    }
)
"""Keys that a deployment must never overwrite in a developer's ``.env``."""

__all__ = [
    "PROTECTED_KEYS",
    "AzdOutputs",
    "declared_env_keys",
    "load_azd_outputs",
    "parse_azd_outputs",
    "write_env_file",
]


@dataclass(frozen=True, slots=True)
class AzdOutputs:
    """The values reported by ``azd env get-values``."""

    values: Mapping[str, str]
    environment_name: str | None = None

    @property
    def is_empty(self) -> bool:
        """True when azd reported nothing, which is normal before provisioning."""
        return not self.values

    @property
    def resource_group(self) -> str | None:
        """The resource group the last deployment created, if reported."""
        return self.get("AZURE_RESOURCE_GROUP")

    def get(self, key: str) -> str | None:
        """Return one output, or ``None`` when it is absent or blank."""
        value = self.values.get(key, "").strip()
        return value or None

    def names(self) -> tuple[str, ...]:
        """Return the output names in sorted order. Values are never exposed.

        Deliberately not called ``keys``. This type is not a mapping, and a
        method named ``keys`` invites callers to treat it as one.
        """
        return tuple(sorted(self.values))


def parse_azd_outputs(raw: str, *, environment_name: str | None = None) -> AzdOutputs:
    """Parse the JSON emitted by ``azd env get-values --output json``.

    Raises:
        AzureEnvironmentError: when the payload is not a JSON object.
    """
    text = raw.strip()
    if not text:
        return AzdOutputs(values=MappingProxyType({}), environment_name=environment_name)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise AzureEnvironmentError("azd did not return valid JSON. Run 'azd env get-values' to see the output.") from (
            error
        )

    if not isinstance(payload, dict):
        raise AzureEnvironmentError(f"azd returned {type(payload).__name__}, but a JSON object was expected.")

    values = {str(key): str(value) for key, value in payload.items() if value is not None}
    return AzdOutputs(values=MappingProxyType(values), environment_name=environment_name)


def load_azd_outputs(environment_name: str | None = None) -> AzdOutputs:
    """Return the outputs of the current azd environment.

    Empty outputs are returned when azd is absent or no environment has been
    provisioned, because that is an expected state before the first run rather
    than an error.
    """
    command = ["azd", "env", "get-values", "--output", "json"]
    if environment_name:
        command.extend(["--environment", environment_name])

    result = run_command(command)
    if not result.ok:
        return AzdOutputs(values=MappingProxyType({}), environment_name=environment_name)

    try:
        return parse_azd_outputs(result.stdout, environment_name=environment_name)
    except AzureEnvironmentError:
        return AzdOutputs(values=MappingProxyType({}), environment_name=environment_name)


def declared_env_keys(example_path: Path) -> list[str]:
    """Return the ordered keys declared in ``.env.example``.

    Only declared keys are ever written, so a stray azd variable cannot silently
    introduce new configuration.
    """
    keys: list[str] = []
    for line in example_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, _ = stripped.partition("=")
        if separator:
            keys.append(key.strip())
    return keys


def write_env_file(env_path: Path, updates: Mapping[str, str]) -> list[str]:
    """Update or append ``updates`` in ``env_path`` and return the changed keys.

    Existing unrelated lines and comments are preserved, so a developer's own
    notes survive a redeployment.
    """
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
    changed: list[str] = []
    seen: set[str] = set()

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, current = stripped.partition("=")
        key = key.strip()
        if not separator or key not in updates:
            continue
        seen.add(key)
        if current.strip() != updates[key]:
            lines[index] = f"{key}={updates[key]}"
            changed.append(key)

    missing = [key for key in updates if key not in seen]
    if missing:
        lines.append("")
        lines.append("# Appended from azd outputs.")
        for key in missing:
            lines.append(f"{key}={updates[key]}")
            changed.append(key)

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed
