"""Exit codes and argument handling for the packaged commands.

The commands are the contract that CI and the notebook rely on, so the three
codes are asserted directly rather than inferred from printed text:

* 0 the check passed
* 1 the check ran and something failed
* 2 the check could not run at all

Nothing here touches Azure. The infrastructure calls are replaced so the exit
code, not the cloud, is what is under test.
"""

from __future__ import annotations

import pytest

from enterprise_agents_on_foundry.cli import EXIT_FAILED, EXIT_OK, EXIT_UNAVAILABLE, database_info, verify
from enterprise_agents_on_foundry.config.settings import Settings
from enterprise_agents_on_foundry.errors import ConfigurationError, UnsafeDatabaseTargetError
from enterprise_agents_on_foundry.infrastructure.azd_outputs import AzdOutputs
from enterprise_agents_on_foundry.infrastructure.environment import (
    EnvironmentHealth,
    PrerequisiteReport,
    ResourceGroupCheck,
)


def test_exit_codes_are_distinct() -> None:
    assert (EXIT_OK, EXIT_FAILED, EXIT_UNAVAILABLE) == (0, 1, 2)


def _health(*problems: str) -> EnvironmentHealth:
    return EnvironmentHealth(
        prerequisites=PrerequisiteReport(checks=()),
        subscription=None,
        resource_group=ResourceGroupCheck(expected="rg-demo", actual="rg-demo", matches=True),
        missing_settings=(),
        problems=tuple(problems),
    )


def _stub_verify(monkeypatch: pytest.MonkeyPatch, health: EnvironmentHealth) -> None:
    monkeypatch.setattr(verify, "load_settings", lambda: Settings())
    monkeypatch.setattr(verify, "load_azd_outputs", lambda: AzdOutputs(values={}))
    monkeypatch.setattr(verify, "report_environment_health", lambda settings, group: health)


def test_verify_returns_ok_when_nothing_is_wrong(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_verify(monkeypatch, _health())

    assert verify.main([]) == EXIT_OK


def test_verify_returns_failed_and_lists_every_problem(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One round trip should surface all problems, not just the first."""
    _stub_verify(monkeypatch, _health("Azure CLI is missing.", "Resource group does not match."))

    exit_code = verify.main([])

    printed = capsys.readouterr().out
    assert exit_code == EXIT_FAILED
    assert "Azure CLI is missing." in printed
    assert "Resource group does not match." in printed


def test_verify_returns_unavailable_when_configuration_cannot_load(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise() -> Settings:
        raise ConfigurationError("DATABASE_MAX_RESULT_ROWS is invalid.")

    monkeypatch.setattr(verify, "load_settings", _raise)

    assert verify.main([]) == EXIT_UNAVAILABLE


def test_verify_rejects_unexpected_arguments() -> None:
    assert verify.main(["--deploy"]) == EXIT_UNAVAILABLE


def test_database_info_returns_unavailable_for_an_unsafe_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refusing to connect is a setup problem, not a test failure."""
    monkeypatch.setattr(database_info, "load_settings", lambda: Settings())

    def _raise(settings: Settings) -> None:
        raise UnsafeDatabaseTargetError("AZURE_SQL_SERVER_FQDN is not set.")

    monkeypatch.setattr(database_info, "resolve_database_target", _raise)

    assert database_info.main([]) == EXIT_UNAVAILABLE


def test_database_info_rejects_unexpected_arguments() -> None:
    assert database_info.main(["--drop"]) == EXIT_UNAVAILABLE
