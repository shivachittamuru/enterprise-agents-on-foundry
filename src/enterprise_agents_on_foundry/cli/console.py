"""Shared console output and exit codes for the project commands.

v0.1 defined ``_print_header`` twice, in two scripts, with the same body. Command
output is a user interface, so it belongs in the package alongside everything
else the commands share.
"""

from __future__ import annotations

from typing import Final

EXIT_OK: Final = 0
"""Everything checked passed."""

EXIT_FAILED: Final = 1
"""A check ran and reported a problem."""

EXIT_UNAVAILABLE: Final = 2
"""A check could not run: configuration, driver, or credential is missing."""

__all__ = ["EXIT_FAILED", "EXIT_OK", "EXIT_UNAVAILABLE", "bullets", "section"]


def section(title: str) -> None:
    """Print a underlined section heading."""
    print(f"\n{title}")
    print("-" * len(title))


def bullets(lines: tuple[str, ...] | list[str]) -> None:
    """Print an indented list."""
    for line in lines:
        print(f"  - {line}")
