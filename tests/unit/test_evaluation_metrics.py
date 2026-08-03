"""Metrics report each dimension separately and never blend them."""

from __future__ import annotations

from enterprise_agents_on_foundry.agents.state import AgentOutcome
from enterprise_agents_on_foundry.database.tool import QueryStatus
from enterprise_agents_on_foundry.evaluation.cases import EvaluationCategory, ExpectedOutcome
from enterprise_agents_on_foundry.evaluation.metrics import summarize
from enterprise_agents_on_foundry.evaluation.results import (
    CaseResult,
    ErrorInfo,
    EvaluationFailureCode,
    TokenUsage,
)

SAFE_EXPECTED = ExpectedOutcome(outcome=AgentOutcome.SUCCEEDED)
UNSAFE_EXPECTED = ExpectedOutcome(outcome=AgentOutcome.UNSUPPORTED, database_calls=0)


def _passed(
    case_id: str,
    *,
    category: EvaluationCategory = EvaluationCategory.QUERY_CORRECTNESS,
    expected: ExpectedOutcome = SAFE_EXPECTED,
    **fields: object,
) -> CaseResult:
    """A passing result carrying whatever evidence a metric needs."""
    return CaseResult(case_id=case_id, category=category, expected=expected, passed=True, **fields)


def _failed(
    case_id: str,
    *codes: EvaluationFailureCode,
    category: EvaluationCategory = EvaluationCategory.QUERY_CORRECTNESS,
    expected: ExpectedOutcome = SAFE_EXPECTED,
    **fields: object,
) -> CaseResult:
    """A failing result carrying at least one code."""
    return CaseResult(
        case_id=case_id,
        category=category,
        expected=expected,
        passed=False,
        failure_codes=codes,
        failure_details=tuple(code.value for code in codes),
        **fields,
    )


def test_totals_and_pass_rate_are_reported() -> None:
    summary = summarize([_passed("a"), _passed("b"), _failed("c", EvaluationFailureCode.TOO_FEW_ROWS)])

    assert summary.total == 3
    assert summary.passed == 2
    assert summary.failed == 1
    assert summary.pass_rate == 0.6667


def test_results_are_split_by_category() -> None:
    summary = summarize(
        [
            _passed("a"),
            _failed("b", EvaluationFailureCode.MISSING_TABLE),
            _passed("c", category=EvaluationCategory.SAFETY, expected=UNSAFE_EXPECTED),
        ]
    )

    assert summary.by_category["query_correctness"].total == 2
    assert summary.by_category["query_correctness"].passed == 1
    assert summary.by_category["query_correctness"].pass_rate == 0.5
    assert summary.by_category["safety"].pass_rate == 1.0


def test_failure_codes_are_counted_and_ranked() -> None:
    summary = summarize(
        [
            _failed("a", EvaluationFailureCode.MISSING_TABLE, EvaluationFailureCode.TOO_FEW_ROWS),
            _failed("b", EvaluationFailureCode.MISSING_TABLE),
        ]
    )

    assert list(summary.failure_counts.items()) == [("missing_table", 2), ("too_few_rows", 1)]


def test_unsafe_rejection_counts_what_reached_the_database() -> None:
    summary = summarize(
        [
            _passed("refused", category=EvaluationCategory.SAFETY, expected=UNSAFE_EXPECTED, database_calls=0),
            _failed(
                "executed",
                EvaluationFailureCode.UNSAFE_STATEMENT_EXECUTED,
                category=EvaluationCategory.SAFETY,
                expected=UNSAFE_EXPECTED,
                database_calls=1,
            ),
        ]
    )

    assert summary.unsafe_rejection_rate == 0.5


def test_safe_acceptance_counts_legitimate_queries_that_ran() -> None:
    summary = summarize(
        [
            _passed("a", query_status=QueryStatus.SUCCESS),
            _failed("b", EvaluationFailureCode.QUERY_STATUS_MISMATCH, query_status=QueryStatus.REJECTED),
        ]
    )

    assert summary.safe_acceptance_rate == 0.5


def test_a_rate_with_no_denominator_is_not_measured() -> None:
    summary = summarize([_passed("a", expected=ExpectedOutcome(outcome=AgentOutcome.CLARIFICATION_REQUIRED))])

    assert summary.unsafe_rejection_rate is None
    assert summary.safe_acceptance_rate is None
    assert summary.repair_success_rate is None


def test_repair_success_counts_only_runs_that_repaired() -> None:
    summary = summarize(
        [
            _passed("a", category=EvaluationCategory.REPAIR, repair_count=1, outcome=AgentOutcome.SUCCEEDED),
            _failed(
                "b",
                EvaluationFailureCode.OUTCOME_MISMATCH,
                category=EvaluationCategory.REPAIR,
                repair_count=2,
                outcome=AgentOutcome.FAILED,
            ),
            _passed("c", repair_count=0, outcome=AgentOutcome.SUCCEEDED),
        ]
    )

    assert summary.repair_success_rate == 0.5


def test_unexpected_exceptions_are_reported_separately_from_failures() -> None:
    summary = summarize(
        [
            _failed("a", EvaluationFailureCode.AGENT_ERROR, error=ErrorInfo(type="RuntimeError", message="boom")),
            _passed("b"),
            _passed("c"),
            _passed("d"),
        ]
    )

    assert summary.unexpected_exception_rate == 0.25


def test_latency_percentiles_use_the_nearest_rank() -> None:
    summary = summarize([_passed(f"case-{index}", total_latency_ms=float(index)) for index in range(1, 21)])

    assert summary.median_latency_ms == 10.0
    assert summary.p95_latency_ms == 19.0


def test_model_calls_and_tokens_are_aggregated() -> None:
    summary = summarize(
        [
            _passed("a", model_calls=2, token_usage=TokenUsage(input_tokens=100, total_tokens=140)),
            _passed("b", model_calls=1, token_usage=TokenUsage(input_tokens=50, total_tokens=60)),
        ]
    )

    assert summary.mean_model_calls == 1.5
    assert summary.max_model_calls == 2
    assert summary.input_tokens == 150
    assert summary.total_tokens == 200
    assert summary.output_tokens is None


def test_unmeasured_tokens_stay_unmeasured() -> None:
    summary = summarize([_passed("a"), _passed("b")])

    assert summary.total_tokens is None
