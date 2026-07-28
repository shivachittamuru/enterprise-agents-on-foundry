"""Repository layout and the contracts other lessons will depend on."""

from __future__ import annotations

from pathlib import Path

import pytest

EXPECTED_DIRECTORIES = [
    "database/queries",
    "database/seeds/adventureworks-lt",
    "docs/adr",
    "docs/architecture",
    "docs/current-state",
    "docs/releases",
    "evals/datasets",
    "infra/modules",
    "notebooks",
    "scripts",
    "src/enterprise_agents_on_foundry/setup",
    "tests",
]

EXPECTED_FILES = [
    ".env.example",
    "README.md",
    "azure.yaml",
    "infra/main.bicep",
    "infra/main.parameters.json",
    "pyproject.toml",
]


@pytest.mark.parametrize("relative", EXPECTED_DIRECTORIES)
def test_expected_directory_exists(repo_root: Path, relative: str) -> None:
    assert (repo_root / relative).is_dir(), f"missing directory: {relative}"


@pytest.mark.parametrize("relative", EXPECTED_FILES)
def test_expected_file_exists(repo_root: Path, relative: str) -> None:
    assert (repo_root / relative).is_file(), f"missing file: {relative}"


IGNORED_PARTS = frozenset({".venv", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__", ".azure"})


def test_no_database_binaries_are_committed(repo_root: Path) -> None:
    """The legacy backend committed three divergent SQLite state files."""
    offenders = [
        path.relative_to(repo_root).as_posix()
        for pattern in ("*.db", "*.sqlite", "*.sqlite3", "*.bacpac")
        for path in repo_root.rglob(pattern)
        if IGNORED_PARTS.isdisjoint(path.parts)
    ]
    assert offenders == [], f"binary database files must not be committed: {offenders}"


def test_env_example_declares_no_secret_values(repo_root: Path) -> None:
    """Placeholders are fine. Populated secrets are not."""
    secret_keys = ("PASSWORD", "SECRET", "_KEY", "APIKEY", "CONNECTION_STRING")
    populated: list[str] = []

    for line in (repo_root / ".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if separator and value.strip() and any(token in key.upper() for token in secret_keys):
            populated.append(key.strip())

    assert populated == [], f"secret-shaped keys must ship empty: {populated}"


def test_env_example_has_no_sql_password_key(repo_root: Path) -> None:
    """The legacy .env.example carried PG_VECTOR_PASSWORD. Entra-only removes it."""
    content = (repo_root / ".env.example").read_text(encoding="utf-8").upper()
    assert "PASSWORD" not in content


def test_gitignore_excludes_local_env_and_sample_data(repo_root: Path) -> None:
    content = (repo_root / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".env", "*.db", "*.bacpac"):
        assert pattern in content, f".gitignore should exclude {pattern}"


def test_sql_query_files_are_read_only(repo_root: Path) -> None:
    """Everything in database/queries must pass the same validator the agent uses."""
    from enterprise_agents_on_foundry.setup.database import assert_read_only_sql

    query_files = sorted((repo_root / "database" / "queries").glob("*.sql"))
    assert query_files, "expected at least one query file"

    for path in query_files:
        assert_read_only_sql(path.read_text(encoding="utf-8"))


def test_grant_script_never_grants_write_access(repo_root: Path) -> None:
    path = repo_root / "database" / "seeds" / "adventureworks-lt" / "grant_readonly_agent_access.sql"
    content = path.read_text(encoding="utf-8").lower()

    assert "db_datareader" in content
    assert "db_datawriter" not in content
    assert "db_owner" not in content
    assert "deny insert, update, delete, alter, execute" in content
