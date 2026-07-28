"""Deployment output parsing and the ``.env`` projection rules.

None of this needs azd installed. Parsing is a pure function over a string, and
file writing is a pure function over a path, which is the point of separating
them from the subprocess call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from enterprise_agents_on_foundry.errors import AzureEnvironmentError
from enterprise_agents_on_foundry.infrastructure.azd_outputs import (
    PROTECTED_KEYS,
    declared_env_keys,
    parse_azd_outputs,
    write_env_file,
)


def test_empty_output_is_not_an_error() -> None:
    """No provisioning yet is an expected state, not a failure."""
    outputs = parse_azd_outputs("")

    assert outputs.is_empty is True
    assert outputs.resource_group is None


def test_values_are_parsed_and_exposed_by_key() -> None:
    outputs = parse_azd_outputs(json.dumps({"AZURE_RESOURCE_GROUP": "rg-demo", "AZURE_LOCATION": "swedencentral"}))

    assert outputs.resource_group == "rg-demo"
    assert outputs.get("AZURE_LOCATION") == "swedencentral"
    assert outputs.names() == ("AZURE_LOCATION", "AZURE_RESOURCE_GROUP")


def test_blank_values_are_treated_as_absent() -> None:
    outputs = parse_azd_outputs(json.dumps({"AZURE_RESOURCE_GROUP": "   "}))

    assert outputs.resource_group is None


def test_null_values_are_dropped() -> None:
    outputs = parse_azd_outputs(json.dumps({"AZURE_RESOURCE_GROUP": None}))

    assert outputs.names() == ()


def test_non_string_values_are_coerced_not_trusted() -> None:
    outputs = parse_azd_outputs(json.dumps({"AZURE_MODEL_CAPACITY": 10}))

    assert outputs.get("AZURE_MODEL_CAPACITY") == "10"


def test_invalid_json_raises_a_typed_error() -> None:
    with pytest.raises(AzureEnvironmentError, match="valid JSON"):
        parse_azd_outputs("not json")


def test_json_array_raises_a_typed_error() -> None:
    """A list would otherwise be indexed as a mapping much later on."""
    with pytest.raises(AzureEnvironmentError, match="object was expected"):
        parse_azd_outputs("[1, 2, 3]")


def test_declared_keys_come_from_the_example_file(tmp_path: Path) -> None:
    example = tmp_path / ".env.example"
    example.write_text("# comment\nAZURE_LOCATION=\n\nAZURE_RESOURCE_GROUP=\nnot-a-key\n", encoding="utf-8")

    assert declared_env_keys(example) == ["AZURE_LOCATION", "AZURE_RESOURCE_GROUP"]


def test_existing_keys_are_updated_in_place(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("# header\nAZURE_LOCATION=westus3\nAZURE_RESOURCE_GROUP=\n", encoding="utf-8")

    changed = write_env_file(env_path, {"AZURE_LOCATION": "swedencentral"})

    content = env_path.read_text(encoding="utf-8")
    assert changed == ["AZURE_LOCATION"]
    assert "AZURE_LOCATION=swedencentral" in content
    assert "# header" in content


def test_unchanged_keys_are_not_reported(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("AZURE_LOCATION=swedencentral\n", encoding="utf-8")

    assert write_env_file(env_path, {"AZURE_LOCATION": "swedencentral"}) == []


def test_missing_keys_are_appended_with_a_marker(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("AZURE_LOCATION=swedencentral\n", encoding="utf-8")

    changed = write_env_file(env_path, {"AZURE_RESOURCE_GROUP": "rg-demo"})

    content = env_path.read_text(encoding="utf-8")
    assert changed == ["AZURE_RESOURCE_GROUP"]
    assert "# Appended from azd outputs." in content
    assert content.endswith("AZURE_RESOURCE_GROUP=rg-demo\n")


def test_comments_and_unrelated_lines_survive(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    original = "# my notes\nMY_OWN_KEY=keep-me\nAZURE_LOCATION=westus3\n"
    env_path.write_text(original, encoding="utf-8")

    write_env_file(env_path, {"AZURE_LOCATION": "swedencentral"})

    content = env_path.read_text(encoding="utf-8")
    assert "# my notes" in content
    assert "MY_OWN_KEY=keep-me" in content


def test_protected_keys_are_declared() -> None:
    """These two are local decisions, so a deployment must not overwrite them."""
    assert "ALLOW_DATABASE_BOOTSTRAP" in PROTECTED_KEYS
    assert "APPLICATIONINSIGHTS_CONNECTION_STRING" in PROTECTED_KEYS
