"""Shared fixtures.

Tests must never pick up a developer's real ``.env`` or Azure environment
variables, so the environment is cleared for every test that builds settings.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from enterprise_agents_on_foundry.setup.config import repository_root

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
