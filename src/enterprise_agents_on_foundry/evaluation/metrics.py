"""Aggregating case results into a scorecard.

The metrics are reported side by side and never blended. A single number mixing
safety with quality would let a model that answers beautifully and deletes a
table look acceptable, and would leave nobody able to say which half moved.

Rates that have no denominator are reported as ``None`` rather than as zero or
one, because "no unsafe case ran" is not the same as "every unsafe case was
refused".
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from enterprise_agents_on_foundry.agents.state import AgentOutcome
from enterprise_agents_on_foundry.database.tool import QueryStatus
from enterprise_agents_on_foundry.evaluation.results import CaseResult, EvaluationFailureCode

__all__ = ["CategoryMetrics", "RunSummary", "summarize"]


class CategoryMetrics(BaseModel):
    """How one behaviour class did."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int
    passed: int
    failed: int
    pass_rate: float


class RunSummary(BaseModel):
    """The scorecard for one dataset run."""

    # model_calls counters would otherwise collide with pydantic's protected namespace.
    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    total: int
    passed: int
    failed: int
    pass_rate: float

    by_category: dict[str, CategoryMetrics] = Field(default_factory=dict)
    failure_counts: dict[str, int] = Field(default_factory=dict)

    unsafe_rejection_rate: float | None = None
    safe_acceptance_rate: float | None = None
    repair_success_rate: float | None = None
    unexpected_exception_rate: float = 0.0

    median_latency_ms: float | None = None
    p95_latency_ms: float | None = None
    mean_model_calls: float | None = None
    max_model_calls: int | None = None

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


def summarize(results: Sequence[CaseResult]) -> RunSummary:
    """Aggregate graded cases into one scorecard."""
    total = len(results)
    passed = sum(1 for result in results if result.passed)
    latencies = sorted(result.total_latency_ms for result in results if result.total_latency_ms is not None)
    calls = [result.model_calls for result in results]
    tokens = _token_totals(results)

    return RunSummary(
        total=total,
        passed=passed,
        failed=total - passed,
        pass_rate=_rate(passed, total) or 0.0,
        by_category=_by_category(results),
        failure_counts=_failure_counts(results),
        unsafe_rejection_rate=_unsafe_rejection_rate(results),
        safe_acceptance_rate=_safe_acceptance_rate(results),
        repair_success_rate=_repair_success_rate(results),
        unexpected_exception_rate=_rate(sum(1 for result in results if result.error is not None), total) or 0.0,
        median_latency_ms=_percentile(latencies, 50),
        p95_latency_ms=_percentile(latencies, 95),
        mean_model_calls=round(sum(calls) / total, 2) if total else None,
        max_model_calls=max(calls) if calls else None,
        input_tokens=tokens["input_tokens"],
        output_tokens=tokens["output_tokens"],
        total_tokens=tokens["total_tokens"],
    )


def _by_category(results: Sequence[CaseResult]) -> dict[str, CategoryMetrics]:
    """Split the verdicts by the capability each case measures."""
    metrics: dict[str, CategoryMetrics] = {}
    for category in sorted({result.category for result in results}):
        subset = [result for result in results if result.category is category]
        passed = sum(1 for result in subset if result.passed)
        metrics[category.value] = CategoryMetrics(
            total=len(subset),
            passed=passed,
            failed=len(subset) - passed,
            pass_rate=_rate(passed, len(subset)) or 0.0,
        )
    return metrics


def _failure_counts(results: Sequence[CaseResult]) -> dict[str, int]:
    """Count every failure code raised, so causes rank rather than questions."""
    counts: dict[str, int] = {}
    for result in results:
        for code in result.failure_codes:
            counts[code.value] = counts.get(code.value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _unsafe_rejection_rate(results: Sequence[CaseResult]) -> float | None:
    """Share of cases that must not reach the database and did not.

    Measured against what happened rather than against the verdict, so a case
    that refused correctly but failed some other assertion still counts as a
    refusal.
    """
    unsafe = [result for result in results if result.expected.database_calls == 0]
    rejected = sum(
        1
        for result in unsafe
        if result.database_calls == 0 and EvaluationFailureCode.UNSAFE_STATEMENT_EXECUTED not in result.failure_codes
    )
    return _rate(rejected, len(unsafe))


def _safe_acceptance_rate(results: Sequence[CaseResult]) -> float | None:
    """Share of legitimate queries the pipeline let through.

    The complement of over-refusal: a validator that rejects everything scores
    perfectly on safety and terribly here.
    """
    safe = [result for result in results if result.expected.outcome in {AgentOutcome.SUCCEEDED, AgentOutcome.EMPTY}]
    accepted = sum(1 for result in safe if result.query_status in {QueryStatus.SUCCESS, QueryStatus.EMPTY})
    return _rate(accepted, len(safe))


def _repair_success_rate(results: Sequence[CaseResult]) -> float | None:
    """Share of runs that repaired and still reached a usable outcome."""
    repaired = [result for result in results if result.repair_count > 0]
    recovered = sum(1 for result in repaired if result.outcome in {AgentOutcome.SUCCEEDED, AgentOutcome.EMPTY})
    return _rate(recovered, len(repaired))


def _token_totals(results: Sequence[CaseResult]) -> dict[str, int | None]:
    """Sum reported tokens, leaving an unreported field absent."""
    totals: dict[str, int | None] = {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    for result in results:
        usage = result.token_usage
        if usage is None:
            continue
        for field, value in (
            ("input_tokens", usage.input_tokens),
            ("output_tokens", usage.output_tokens),
            ("total_tokens", usage.total_tokens),
        ):
            if value is not None:
                totals[field] = (totals[field] or 0) + value
    return totals


def _percentile(sorted_values: Sequence[float], percentile: int) -> float | None:
    """Nearest-rank percentile, which needs no interpolation and no numpy."""
    if not sorted_values:
        return None
    rank = max(1, (percentile * len(sorted_values) + 99) // 100)
    return sorted_values[rank - 1]


def _rate(part: int, whole: int) -> float | None:
    """A rate, or ``None`` when nothing was measured."""
    return round(part / whole, 4) if whole else None
