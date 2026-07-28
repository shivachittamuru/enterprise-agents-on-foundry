"""Command-line entry points.

Each command is a thin adapter: parse nothing more than it must, call package
functions, print, and return an exit code. No logic lives here that a notebook or
a test could not reach another way.
"""

from enterprise_agents_on_foundry.cli.console import EXIT_FAILED, EXIT_OK, EXIT_UNAVAILABLE

__all__ = ["EXIT_FAILED", "EXIT_OK", "EXIT_UNAVAILABLE"]
