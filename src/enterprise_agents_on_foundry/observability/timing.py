"""Wall-clock timing.

Database queries, environment checks, and measurement capture all need to answer
"how long did that take". One primitive serves all three, so elapsed time is
computed the same way and rounded the same way everywhere.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

__all__ = ["Stopwatch", "timed"]


class Stopwatch:
    """Wall-clock elapsed time for a single block."""

    __slots__ = ("_elapsed_ms", "_started", "failed")

    def __init__(self) -> None:
        self._started = time.perf_counter()
        self._elapsed_ms: float | None = None
        self.failed = False

    def stop(self) -> float:
        """Freeze and return the elapsed milliseconds."""
        if self._elapsed_ms is None:
            self._elapsed_ms = round((time.perf_counter() - self._started) * 1000, 1)
        return self._elapsed_ms

    @property
    def milliseconds(self) -> float:
        """Elapsed milliseconds, measured live until :meth:`stop` is called."""
        if self._elapsed_ms is not None:
            return self._elapsed_ms
        return round((time.perf_counter() - self._started) * 1000, 1)


@contextmanager
def timed() -> Iterator[Stopwatch]:
    """Time a block, recording the duration even when the block raises.

    A failed operation still produces a data point. Discarding it would make an
    error look like an instant success in the measurement set.
    """
    stopwatch = Stopwatch()
    try:
        yield stopwatch
    except Exception:
        stopwatch.failed = True
        raise
    finally:
        stopwatch.stop()
