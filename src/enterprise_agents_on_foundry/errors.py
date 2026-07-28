"""The project exception hierarchy.

v0.1 raised four unrelated exception types from four modules, plus a builtin
``PermissionError`` for the database bootstrap gate. A caller that wanted to
handle "anything this project raised" had no way to say so, and a caller that
caught ``ValueError`` silently swallowed unrelated bugs.

One base class fixes both problems. The hierarchy stays flat and small: five
concrete errors, one per boundary the project actually crosses.
"""

from __future__ import annotations

__all__ = [
    "AzureEnvironmentError",
    "ConfigurationError",
    "DatabaseConnectionError",
    "EaofError",
    "QueryValidationError",
    "UnsafeDatabaseTargetError",
]


class EaofError(Exception):
    """Base class for every error this project raises deliberately."""


class ConfigurationError(EaofError):
    """Configuration is missing, malformed, or names an unsupported value."""


class AzureEnvironmentError(EaofError):
    """The Azure environment cannot be read or does not match configuration."""


class UnsafeDatabaseTargetError(EaofError):
    """The configured database target is missing, malformed, or not permitted."""


class DatabaseConnectionError(EaofError):
    """A connection to Azure SQL could not be established or used."""


class QueryValidationError(EaofError):
    """A statement is not a single, read-only query."""
