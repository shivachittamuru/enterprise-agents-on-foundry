"""Grading is deterministic and every failed assertion names itself."""

from __future__ import annotations

from dataclasses import replace

import pytest

from enterprise_agents_on_foundry.agents.state import (
    AgentOutcome,
    AgentOutput,
    GenerationDisposition,
    ModelCallMetadata,
)
from enterprise_agents_on_foundry.database.tool import QueryStatus
from enterprise_agents_on_foundry.evaluation.cases import EvaluationCase, EvaluationCategory, ExpectedOutcome
from enterprise_agents_on_foundry.evaluation.checks import grade
from enterprise_agents_on_foundry.evaluation.harness import RunEvidence
from enterprise_agents_on_foundry.evaluation.results import EvaluationFailureCode

QUESTION = "How many products are there?"

BASE_OUTPUT = AgentOutput(
    outcome=AgentOutcome.SUCCEEDED,
    question=QUESTION,
    answer="There are 295 products.",
    sql="SELECT COUNT(*) AS ProductCount FROM SalesLT.Product",
    row_count=1,
    columns=("ProductCount",),
    query_status=QueryStatus.SUCCESS,
    model_calls=(
        ModelCallMetadata(purpose="generate_sql", latency_ms=12.0),
        ModelCallMetadata(purpose="compose_answer", latency_ms=8.0),
    ),
)

BASE_EXPECTED = ExpectedOutcome(
    disposition=GenerationDisposition.READY,
    outcome=AgentOutcome.SUCCEEDED,
    query_status=QueryStatus.SUCCESS,
)


def _case(
    expected: ExpectedOutcome, *, category: EvaluationCategory = EvaluationCategory.QUERY_CORRECTNESS
) -> EvaluationCase:
    """A case that asserts exactly what a test is about."""
    return EvaluationCase(id="case-001", category=category, question=QUESTION, expected=expected)


def _evidence(
    output: AgentOutput | None = BASE_OUTPUT,
    *,
    database_calls: int = 1,
    total_latency_ms: float = 20.0,
    error: Exception | None = None,
) -> RunEvidence:
    """Evidence for one run, with only the field under test changed."""
    return RunEvidence(
        output=output,
        database_calls=database_calls,
        total_latency_ms=total_latency_ms,
        stage_latencies_ms={"generate_sql": 12.0, "compose_answer": 8.0},
        error=error,
    )


def _codes(expected: ExpectedOutcome, evidence: RunEvidence) -> tuple[EvaluationFailureCode, ...]:
    """Grade one run and return only its failure codes."""
    return grade(_case(expected), evidence).failure_codes


def test_a_run_that_meets_every_assertion_passes() -> None:
    expected = BASE_EXPECTED.model_copy(
        update={
            "required_sql_patterns": (r"\bcount\s*\(",),
            "required_tables": ("SalesLT.Product",),
            "expected_columns": ("ProductCount",),
            "min_rows": 1,
            "max_rows": 1,
            "required_answer_text": ("295",),
            "max_repairs": 0,
            "database_calls": 1,
            "max_model_calls": 2,
            "max_latency_ms": 1000.0,
        }
    )
    result = grade(_case(expected), _evidence())

    assert result.passed
    assert result.failure_codes == ()
    assert result.failure_details == ()


def test_a_passing_result_records_the_evidence_it_graded() -> None:
    result = grade(_case(BASE_EXPECTED), _evidence())

    assert result.case_id == "case-001"
    assert result.disposition is GenerationDisposition.READY
    assert result.query_status is QueryStatus.SUCCESS
    assert result.database_calls == 1
    assert result.columns == ("ProductCount",)
    assert result.model_calls == 2
    assert result.total_latency_ms == 20.0
    assert result.stage_latencies_ms == {"generate_sql": 12.0, "compose_answer": 8.0}


def test_disposition_mismatch_is_reported() -> None:
    expected = ExpectedOutcome(disposition=GenerationDisposition.UNSUPPORTED, outcome=AgentOutcome.UNSUPPORTED)
    codes = _codes(expected, _evidence())

    assert EvaluationFailureCode.DISPOSITION_MISMATCH in codes
    assert EvaluationFailureCode.OUTCOME_MISMATCH in codes


def test_outcome_mismatch_is_reported() -> None:
    output = replace(BASE_OUTPUT, outcome=AgentOutcome.FAILED)
    codes = _codes(ExpectedOutcome(outcome=AgentOutcome.SUCCEEDED), _evidence(output))

    assert codes == (EvaluationFailureCode.OUTCOME_MISMATCH,)


def test_query_status_mismatch_is_reported() -> None:
    expected = ExpectedOutcome(outcome=AgentOutcome.SUCCEEDED, query_status=QueryStatus.SUCCESS)
    output = replace(BASE_OUTPUT, query_status=None)

    assert _codes(expected, _evidence(output)) == (EvaluationFailureCode.QUERY_STATUS_MISMATCH,)


def test_a_clarification_without_a_question_is_reported() -> None:
    expected = ExpectedOutcome(
        disposition=GenerationDisposition.CLARIFICATION_REQUIRED,
        outcome=AgentOutcome.CLARIFICATION_REQUIRED,
    )
    output = AgentOutput(outcome=AgentOutcome.CLARIFICATION_REQUIRED, question=QUESTION)

    assert _codes(expected, _evidence(output, database_calls=0)) == (EvaluationFailureCode.MISSING_CLARIFICATION,)


def test_a_missing_statement_is_reported_once_rather_than_per_pattern() -> None:
    expected = ExpectedOutcome(
        outcome=AgentOutcome.FAILED,
        required_sql_patterns=(r"\bselect\b", r"\bcount\b"),
        required_tables=("SalesLT.Product",),
    )
    output = replace(BASE_OUTPUT, outcome=AgentOutcome.FAILED, sql=None, query_status=None)

    assert _codes(expected, _evidence(output)) == (EvaluationFailureCode.MISSING_SQL,)


def test_a_required_pattern_that_is_absent_is_reported() -> None:
    expected = BASE_EXPECTED.model_copy(update={"required_sql_patterns": (r"\border\s+by\b",)})

    assert _codes(expected, _evidence()) == (EvaluationFailureCode.MISSING_SQL_PATTERN,)


def test_required_patterns_ignore_case() -> None:
    expected = BASE_EXPECTED.model_copy(update={"required_sql_patterns": (r"\bSELECT\b",)})
    output = replace(BASE_OUTPUT, sql="select count(*) from SalesLT.Product")

    assert _codes(expected, _evidence(output)) == ()


def test_a_forbidden_pattern_that_reached_the_database_reports_both_codes() -> None:
    expected = BASE_EXPECTED.model_copy(update={"forbidden_sql_patterns": (r"\bcount\b",)})
    codes = _codes(expected, _evidence())

    assert codes == (
        EvaluationFailureCode.FORBIDDEN_SQL_PATTERN,
        EvaluationFailureCode.UNSAFE_STATEMENT_EXECUTED,
    )


def test_a_forbidden_pattern_that_never_ran_is_not_an_execution() -> None:
    expected = ExpectedOutcome(outcome=AgentOutcome.REJECTED, forbidden_sql_patterns=(r"\bdelete\b",))
    output = replace(
        BASE_OUTPUT,
        outcome=AgentOutcome.REJECTED,
        sql="DELETE FROM SalesLT.Customer",
        query_status=None,
        row_count=None,
    )

    assert _codes(expected, _evidence(output, database_calls=0)) == (EvaluationFailureCode.FORBIDDEN_SQL_PATTERN,)


def test_a_missing_table_is_reported() -> None:
    expected = BASE_EXPECTED.model_copy(update={"required_tables": ("SalesLT.SalesOrderHeader",)})

    assert _codes(expected, _evidence()) == (EvaluationFailureCode.MISSING_TABLE,)


def test_a_missing_column_is_reported() -> None:
    expected = BASE_EXPECTED.model_copy(update={"expected_columns": ("ProductCount", "Margin")})

    assert _codes(expected, _evidence()) == (EvaluationFailureCode.MISSING_COLUMN,)


@pytest.mark.parametrize(
    ("update", "row_count", "code"),
    [
        ({"min_rows": 5}, 1, EvaluationFailureCode.TOO_FEW_ROWS),
        ({"min_rows": 1}, None, EvaluationFailureCode.TOO_FEW_ROWS),
        ({"max_rows": 1}, 12, EvaluationFailureCode.TOO_MANY_ROWS),
    ],
)
def test_row_bounds_are_enforced(update: dict[str, int], row_count: int | None, code: EvaluationFailureCode) -> None:
    expected = BASE_EXPECTED.model_copy(update=update)

    assert _codes(expected, _evidence(replace(BASE_OUTPUT, row_count=row_count))) == (code,)


def test_a_missing_answer_is_reported_once() -> None:
    expected = BASE_EXPECTED.model_copy(update={"required_answer_text": ("295", "products")})

    assert _codes(expected, _evidence(replace(BASE_OUTPUT, answer=None))) == (EvaluationFailureCode.MISSING_ANSWER,)


def test_required_answer_text_is_matched_case_insensitively() -> None:
    expected = BASE_EXPECTED.model_copy(update={"required_answer_text": ("PRODUCTS", "missing phrase")})

    assert _codes(expected, _evidence()) == (EvaluationFailureCode.MISSING_ANSWER_TEXT,)


def test_forbidden_answer_text_is_reported() -> None:
    expected = BASE_EXPECTED.model_copy(update={"forbidden_answer_text": ("295",)})

    assert _codes(expected, _evidence()) == (EvaluationFailureCode.FORBIDDEN_ANSWER_TEXT,)


@pytest.mark.parametrize(
    ("update", "repairs", "code"),
    [
        ({"min_repairs": 1}, 0, EvaluationFailureCode.TOO_FEW_REPAIRS),
        ({"max_repairs": 0}, 1, EvaluationFailureCode.TOO_MANY_REPAIRS),
    ],
)
def test_repair_bounds_are_enforced(update: dict[str, int], repairs: int, code: EvaluationFailureCode) -> None:
    expected = BASE_EXPECTED.model_copy(update=update)

    assert _codes(expected, _evidence(replace(BASE_OUTPUT, repair_attempts=repairs))) == (code,)


def test_an_unexpected_database_call_is_reported() -> None:
    expected = BASE_EXPECTED.model_copy(update={"database_calls": 1})

    assert _codes(expected, _evidence(database_calls=2)) == (EvaluationFailureCode.DATABASE_CALL_COUNT_MISMATCH,)


def test_the_model_call_budget_is_enforced() -> None:
    expected = BASE_EXPECTED.model_copy(update={"max_model_calls": 1})

    assert _codes(expected, _evidence()) == (EvaluationFailureCode.MODEL_CALL_BUDGET_EXCEEDED,)


def test_the_latency_budget_is_optional_and_enforced_when_set() -> None:
    expected = BASE_EXPECTED.model_copy(update={"max_latency_ms": 5.0})

    assert _codes(expected, _evidence(total_latency_ms=20.0)) == (EvaluationFailureCode.LATENCY_BUDGET_EXCEEDED,)
    assert _codes(BASE_EXPECTED, _evidence(total_latency_ms=20.0)) == ()


def test_every_failed_assertion_produces_its_own_code() -> None:
    expected = BASE_EXPECTED.model_copy(
        update={
            "required_tables": ("SalesLT.SalesOrderHeader",),
            "expected_columns": ("Margin",),
            "database_calls": 0,
            "max_model_calls": 1,
        }
    )
    result = grade(_case(expected), _evidence())

    assert not result.passed
    assert set(result.failure_codes) == {
        EvaluationFailureCode.MISSING_TABLE,
        EvaluationFailureCode.MISSING_COLUMN,
        EvaluationFailureCode.DATABASE_CALL_COUNT_MISMATCH,
        EvaluationFailureCode.MODEL_CALL_BUDGET_EXCEEDED,
    }
    assert len(result.failure_details) == len(result.failure_codes)


def test_an_agent_exception_is_one_recorded_failure() -> None:
    result = grade(_case(BASE_EXPECTED), _evidence(None, database_calls=0, error=RuntimeError("the model timed out")))

    assert result.failure_codes == (EvaluationFailureCode.AGENT_ERROR,)
    assert result.error is not None
    assert result.error.type == "RuntimeError"
    assert result.error.message == "the model timed out"


def test_a_missing_output_without_an_exception_is_a_harness_failure() -> None:
    result = grade(_case(BASE_EXPECTED), _evidence(None, database_calls=0))

    assert result.failure_codes == (EvaluationFailureCode.HARNESS_ERROR,)
    assert result.error is None


def test_reported_tokens_are_summed_and_absent_tokens_stay_unmeasured() -> None:
    output = replace(
        BASE_OUTPUT,
        model_calls=(
            ModelCallMetadata(purpose="generate_sql", input_tokens=100, total_tokens=140),
            ModelCallMetadata(purpose="compose_answer", input_tokens=50, total_tokens=70),
        ),
    )
    usage = grade(_case(BASE_EXPECTED), _evidence(output)).token_usage

    assert usage is not None
    assert usage.input_tokens == 150
    assert usage.total_tokens == 210
    assert usage.output_tokens is None
    assert grade(_case(BASE_EXPECTED), _evidence()).token_usage is None


def test_a_rejected_statement_is_recorded_as_a_validator_rejection() -> None:
    expected = ExpectedOutcome(outcome=AgentOutcome.REJECTED)
    output = replace(
        BASE_OUTPUT,
        outcome=AgentOutcome.REJECTED,
        query_status=QueryStatus.REJECTED,
        failure_reason="DELETE is not permitted.",
        row_count=None,
    )
    result = grade(_case(expected), _evidence(output, database_calls=0))

    assert result.validation_passed is False
    assert result.validation_error == "DELETE is not permitted."


def test_a_case_that_produced_no_statement_records_no_validator_verdict() -> None:
    expected = ExpectedOutcome(disposition=GenerationDisposition.UNSUPPORTED, outcome=AgentOutcome.UNSUPPORTED)
    output = AgentOutput(outcome=AgentOutcome.UNSUPPORTED, question=QUESTION)
    result = grade(_case(expected, category=EvaluationCategory.SAFETY), _evidence(output, database_calls=0))

    assert result.passed
    assert result.validation_passed is None
    assert result.validation_error is None
