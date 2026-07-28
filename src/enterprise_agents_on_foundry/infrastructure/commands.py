"""One safe way to run an external command.

v0.1 defined three near-identical ``subprocess.run`` wrappers, in ``prereqs``,
``azure_env``, and ``model_catalog``. They differed only in timeout and in how
they reported failure, which is exactly the kind of drift a shared helper
prevents.

Every call resolves the executable through :func:`shutil.which` and passes a
fixed argument list. No command is ever built from a string, so there is no
shell to inject into.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Final

from enterprise_agents_on_foundry.errors import AzureEnvironmentError

DEFAULT_TIMEOUT_SECONDS: Final = 120
"""Long enough for an Azure CLI round trip, short enough to fail a wedged shell."""

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "CommandResult",
    "run_command",
    "run_json_command",
]


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The outcome of one external command."""

    command: str
    found: bool
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """True when the executable was found and exited zero."""
        return self.found and self.exit_code == 0

    @property
    def detail(self) -> str:
        """The most useful single line of output, for an error message."""
        for stream in (self.stderr, self.stdout):
            for line in stream.splitlines():
                stripped = line.strip()
                if stripped:
                    return stripped
        return f"'{self.command}' exited with code {self.exit_code}."


def run_command(arguments: list[str], *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> CommandResult:
    """Run ``arguments`` and return the result without raising."""
    printable = " ".join(arguments)
    executable = shutil.which(arguments[0])
    if executable is None:
        return CommandResult(
            command=printable,
            found=False,
            exit_code=127,
            stdout="",
            stderr=f"'{arguments[0]}' was not found on PATH.",
        )

    try:
        completed = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            [executable, *arguments[1:]],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (subprocess.SubprocessError, OSError) as error:
        return CommandResult(command=printable, found=True, exit_code=1, stdout="", stderr=str(error))

    return CommandResult(
        command=printable,
        found=True,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_json_command(arguments: list[str], *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> object:
    """Run ``arguments`` and parse stdout as JSON.

    Raises:
        AzureEnvironmentError: when the command is missing, fails, or emits
            output that is not JSON.
    """
    result = run_command(arguments, timeout_seconds=timeout_seconds)
    if not result.ok:
        raise AzureEnvironmentError(f"'{result.command}' failed: {result.detail}")

    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError as error:
        raise AzureEnvironmentError(f"Could not parse JSON from '{result.command}'.") from error
