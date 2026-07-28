"""Shared fixtures for the whole suite.

Two rules hold everywhere:

* Tests must never pick up a developer's real ``.env`` or exported Azure
  variables, so the environment is cleared for anything that builds settings.
* Anything that touches Azure is marked ``azure`` and skips rather than fails
  when the environment is not provisioned. ``uv run pytest -m "not azure"`` is
  the default loop and needs no cloud access at all.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from enterprise_agents_on_foundry.config.settings import Settings, load_settings, repository_root
from enterprise_agents_on_foundry.errors import ConfigurationError

_PREFIXES = ("AZURE_", "ENABLE_", "DATABASE_", "DATASET_", "PROJECT_", "ENVIRONMENT_", "ALLOW_", "APPLICATIONINSIGHTS_")


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove configuration variables so tests observe declared defaults."""
    for name in list(os.environ):
        if name.upper().startswith(_PREFIXES):
            monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture
def empty_env_file(tmp_path: Path) -> Path:
    """Return a path to a non-existent ``.env`` so file loading is a no-op."""
    return tmp_path / "absent.env"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Return the repository root."""
    return repository_root()


@pytest.fixture(scope="session")
def azure_settings() -> Settings:
    """Return settings loaded from the developer's real environment.

    Integration tests use this instead of the isolated fixtures above. It skips
    rather than fails when the environment has not been provisioned, so a fresh
    clone can still run the full command without a subscription.
    """
    try:
        settings = load_settings()
    except ConfigurationError as error:  # pragma: no cover - workstation dependent
        pytest.skip(f"Configuration is not loadable: {error}")

    missing = settings.missing_provisioning_outputs()
    if missing:  # pragma: no cover - workstation dependent
        pytest.skip(f"Environment is not provisioned. Missing: {', '.join(missing)}")
    return settings
