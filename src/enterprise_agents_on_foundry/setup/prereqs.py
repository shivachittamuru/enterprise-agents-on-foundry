"""Workstation prerequisite checks.

Replaces the "install Conda, then read the README" onboarding of the legacy
project with a deterministic, machine-readable report. Every check shells out to
a version command using an argument list, never a shell string.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Final

_COMMAND_TIMEOUT_SECONDS: Final = 60

MINIMUM_PYTHON: Final[tuple[int, int]] = (3, 12)


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

    checks: tuple[ToolCheck, ...] = field(default_factory=tuple)

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


def _first_line(text: str) -> str:
    """Return the first non-empty line, trimmed."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _run_version_command(command: list[str]) -> tuple[bool, str | None, str | None]:
    """Run a version command safely and summarise the result."""
    executable = shutil.which(command[0])
    if executable is None:
        return False, None, f"'{command[0]}' was not found on PATH."

    try:
        completed = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            [executable, *command[1:]],
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.SubprocessError, OSError) as error:
        return False, None, f"Failed to run '{command[0]}': {error}"

    if completed.returncode != 0:
        detail = _first_line(completed.stderr) or _first_line(completed.stdout)
        return False, None, detail or f"'{command[0]}' exited with code {completed.returncode}."

    return True, _first_line(completed.stdout) or _first_line(completed.stderr), None


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


def check_tool(name: str, args: list[str], *, required: bool = True) -> ToolCheck:
    """Check a single tool by running its version command."""
    found, version, detail = _run_version_command([name, *args])
    return ToolCheck(name=name, required=required, found=found, version=version, detail=detail)


def check_bicep() -> ToolCheck:
    """Check the Bicep CLI bundled with the Azure CLI.

    Bicep is normally installed as an Azure CLI extension rather than a
    standalone binary on PATH, so it is checked through ``az bicep version``.
    """
    found, version, detail = _run_version_command(["az", "bicep", "version"])
    return ToolCheck(name="bicep", required=True, found=found, version=version, detail=detail)


def check_all_prerequisites() -> PrerequisiteReport:
    """Check every tool the v0.1 workflow depends on."""
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
