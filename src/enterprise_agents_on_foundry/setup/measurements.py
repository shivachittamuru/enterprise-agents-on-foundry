"""Collection and serialisation of setup measurements.

The v0.1 release claims a before/after improvement. That claim is only credible
if the numbers behind it are captured the same way every time, so measurement is
treated as repository code rather than as notebook scratch work.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Measurement:
    """A single named observation."""

    name: str
    value: float | int | str | None
    unit: str = "count"
    category: str = "setup"
    note: str | None = None


@dataclass
class MeasurementSet:
    """An ordered collection of measurements with provenance."""

    release: str = "v0.1"
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
    ) -> None:
        """Record one observation."""
        self.measurements.append(Measurement(name=name, value=value, unit=unit, category=category, note=note))

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
    started = time.perf_counter()
    failed = False
    try:
        yield
    except Exception:
        failed = True
        raise
    finally:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        suffix = " (failed)" if failed else ""
        measurements.add(
            name,
            elapsed_ms,
            unit="ms",
            category=category,
            note=f"{note}{suffix}" if note else (suffix.strip() or None),
        )
