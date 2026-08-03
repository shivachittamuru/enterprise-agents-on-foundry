"""Loading and selecting evaluation cases.

JSONL rather than one JSON array, because a dataset grows one line at a time and
a bad line should name itself in a diff and in an error message. Every failure
reports the line number, so fixing a dataset never requires bisecting it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from enterprise_agents_on_foundry.errors import EvaluationDatasetError
from enterprise_agents_on_foundry.evaluation.cases import EvaluationCase, EvaluationCategory

__all__ = [
    "filter_cases",
    "load_cases",
    "parse_cases",
    "read_json_lines",
]


def load_cases(path: Path) -> tuple[EvaluationCase, ...]:
    """Read one JSONL dataset from disk."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise EvaluationDatasetError(f"{path}: cannot be read: {error}") from error
    return parse_cases(text.splitlines(), source=str(path))


def parse_cases(lines: Iterable[str], *, source: str = "<dataset>") -> tuple[EvaluationCase, ...]:
    """Parse JSONL content into cases, rejecting the whole dataset on any bad line.

    Partial loading is not offered. A dataset that silently drops a safety case
    would report a higher pass rate than the agent earned.
    """
    cases: list[EvaluationCase] = []
    seen: dict[str, int] = {}

    for number, payload in read_json_lines(lines, source=source):
        try:
            case = EvaluationCase.model_validate(payload)
        except ValidationError as error:
            raise EvaluationDatasetError(f"{source}:{number}: invalid case: {error}") from error

        if case.id in seen:
            raise EvaluationDatasetError(
                f"{source}:{number}: duplicate case id {case.id!r}, first seen on line {seen[case.id]}"
            )
        seen[case.id] = number
        cases.append(case)

    if not cases:
        raise EvaluationDatasetError(f"{source}: contains no cases")
    return tuple(cases)


def read_json_lines(lines: Iterable[str], *, source: str) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield each non-blank line as a numbered JSON object.

    Shared by the dataset and the deterministic scripts, so a malformed line is
    reported the same way whichever file it is in.
    """
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as error:
            raise EvaluationDatasetError(f"{source}:{number}: not valid JSON: {error.msg}") from error
        if not isinstance(payload, dict):
            raise EvaluationDatasetError(f"{source}:{number}: expected a JSON object")
        yield number, payload


def filter_cases(
    cases: Iterable[EvaluationCase],
    *,
    case_ids: Iterable[str] | None = None,
    categories: Iterable[EvaluationCategory] | None = None,
    tags: Iterable[str] | None = None,
) -> tuple[EvaluationCase, ...]:
    """Select the cases to run.

    Each filter matches on any of its values, and the filters combine with and,
    so "the safety cases that touch joins" is one call. ``None`` means no filter,
    which is not the same as an empty selection.
    """
    wanted_ids = frozenset(case_ids) if case_ids is not None else None
    wanted_categories = frozenset(categories) if categories is not None else None
    wanted_tags = frozenset(tag.strip().casefold() for tag in tags) if tags is not None else None

    return tuple(
        case
        for case in cases
        if (wanted_ids is None or case.id in wanted_ids)
        and (wanted_categories is None or case.category in wanted_categories)
        and (wanted_tags is None or bool(wanted_tags & frozenset(case.tags)))
    )
