"""Collection and serialisation of release measurements.

Each release claims a before and after improvement. That claim is only credible
if the numbers behind it are captured the same way every time, so measurement is
treated as repository code rather than as notebook scratch work.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from enterprise_agents_on_foundry.observability.timing import Stopwatch

__all__ = ["Measurement", "MeasurementSet", "time_operation"]


@dataclass(frozen=True, slots=True)
class Measurement:
    """A single named observation.

    ``baseline`` carries the value the previous release recorded for the same
    name. Keeping it alongside the current value means a before and after claim
    cannot drift from the number it was derived from.
    """

    name: str
    value: float | int | str | None
    unit: str = "count"
    category: str = "setup"
    note: str | None = None
    baseline: float | int | str | None = None


@dataclass
class MeasurementSet:
    """An ordered collection of measurements with provenance."""

    release: str = "v0.2"
    captured_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    measurements: list[Measurement] = field(default_factory=list)

    def add(
        self,
        name: str,
        value: float | int | str | None,
        *,
        unit: str = "count",
        category: str = "setup",
        note: str | None = None,
        baseline: float | int | str | None = None,
    ) -> None:
        """Record one observation, optionally with the previous release's value."""
        self.measurements.append(
            Measurement(name=name, value=value, unit=unit, category=category, note=note, baseline=baseline)
        )

    def get(self, name: str) -> Measurement | None:
        """Return the first measurement with ``name``, if any."""
        return next((item for item in self.measurements if item.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "release": self.release,
            "capturedAt": self.captured_at,
            "measurements": [asdict(item) for item in self.measurements],
        }

    def as_rows(self) -> list[tuple[str, str, str, str]]:
        """Return ``(category, name, value, unit)`` rows for display."""
        return [
            (item.category, item.name, "-" if item.value is None else str(item.value), item.unit)
            for item in self.measurements
        ]

    def format_table(self) -> str:
        """Render every measurement as fixed-width text, with baselines where known."""
        header = f"{'category':<12}{'measure':<34}{'before':>10}{'after':>10}  unit"
        lines = [header, "-" * len(header)]
        for item in self.measurements:
            before = "-" if item.baseline is None else str(item.baseline)
            after = "-" if item.value is None else str(item.value)
            lines.append(f"{item.category:<12}{item.name:<34}{before:>10}{after:>10}  {item.unit}")
        return "\n".join(lines)

    def write_json(self, path: Path) -> Path:
        """Write the set to ``path`` as indented JSON and return the path."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return path


@contextmanager
def time_operation(
    measurements: MeasurementSet,
    name: str,
    *,
    category: str = "setup",
    note: str | None = None,
) -> Iterator[None]:
    """Time a block and record the elapsed milliseconds.

    The measurement is recorded even when the block raises, so a failed step
    still contributes a data point rather than silently disappearing.
    """
    stopwatch = Stopwatch()
    failed = False
    try:
        yield
    except Exception:
        failed = True
        raise
    finally:
        suffix = " (failed)" if failed else ""
        measurements.add(
            name,
            stopwatch.stop(),
            unit="ms",
            category=category,
            note=f"{note}{suffix}" if note else (suffix.strip() or None),
        )
