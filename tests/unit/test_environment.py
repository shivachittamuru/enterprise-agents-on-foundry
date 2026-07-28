"""Environment checks that can be decided without calling Azure."""

from __future__ import annotations

import pytest

from enterprise_agents_on_foundry.infrastructure.commands import CommandResult, run_command
from enterprise_agents_on_foundry.infrastructure.environment import (
    MINIMUM_PYTHON,
    check_expected_resource_group,
    check_python,
)


def test_running_interpreter_meets_the_declared_minimum() -> None:
    check = check_python()

    assert MINIMUM_PYTHON == (3, 12)
    assert check.found is True


def test_matching_resource_group_passes() -> None:
    check = check_expected_resource_group("rg-eaof-dev", "rg-eaof-dev")

    assert check.matches is True
    assert "matches" in check.message


def test_resource_group_comparison_ignores_case() -> None:
    """Azure resource group names are case-insensitive."""
    assert check_expected_resource_group("RG-EAOF-DEV", "rg-eaof-dev").matches is True


def test_mismatched_resource_group_refuses_to_guess() -> None:
    """Validating against the wrong group is how the wrong environment is changed."""
    check = check_expected_resource_group("rg-eaof-dev", "rg-eaof-prod")

    assert check.matches is False
    assert "Refusing to guess" in check.message
    assert "rg-eaof-prod" in check.message


def test_unset_resource_group_says_what_to_run() -> None:
    check = check_expected_resource_group(None, "rg-eaof-dev")

    assert check.matches is False
    assert "azd provision" in check.message


def test_missing_deployment_output_is_reported_separately() -> None:
    check = check_expected_resource_group("rg-eaof-dev", None)

    assert check.matches is False
    assert "no azd deployment output" in check.message


@pytest.mark.parametrize("name", ["rg with spaces", "rg/slash", "", "." * 3, "a" * 91])
def test_malformed_resource_group_names_are_rejected(name: str) -> None:
    assert check_expected_resource_group(name, name).matches is False


def test_missing_executable_returns_a_result_instead_of_raising() -> None:
    """Callers build reports, so a missing tool must not abort the report."""
    result = run_command(["definitely-not-a-real-command", "--version"])

    assert isinstance(result, CommandResult)
    assert result.found is False
    assert result.ok is False
    assert "was not found on PATH" in result.detail


def test_successful_command_reports_output() -> None:
    result = run_command(["python", "-c", "print('hello')"])

    assert result.ok is True
    assert result.stdout.strip() == "hello"


def test_failing_command_reports_the_exit_code() -> None:
    result = run_command(["python", "-c", "raise SystemExit(3)"])

    assert result.ok is False
    assert result.exit_code == 3
