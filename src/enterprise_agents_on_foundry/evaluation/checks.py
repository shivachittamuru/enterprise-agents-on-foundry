"""Grading one run against one expectation.

Every check is a comparison between a field the case declared and a field the
agent produced. No model grades anything, nothing is scored on similarity, and
the same evidence always produces the same verdict.

Each failed assertion contributes its own code and its own detail line. A run
that produced the wrong outcome, ran an extra query, and exceeded its model
budget reports three findings, because collapsing them into one would hide two
of the three regressions.
"""

from __future__ import annotations

import re

from enterprise_agents_on_foundry.agents.state import AgentOutcome, AgentOutput, GenerationDisposition
from enterprise_agents_on_foundry.database.tool import QueryStatus
from enterprise_agents_on_foundry.evaluation.cases import EvaluationCase, ExpectedOutcome
from enterprise_agents_on_foundry.evaluation.harness import RunEvidence
from enterprise_agents_on_foundry.evaluation.results import (
    CaseResult,
    ErrorInfo,
    EvaluationFailureCode,
    TokenUsage,
)

Finding = tuple[EvaluationFailureCode, str]

__all__ = ["grade"]


def grade(case: EvaluationCase, evidence: RunEvidence) -> CaseResult:
    """Turn one run into a verdict and its evidence."""
    output = evidence.output
    findings: list[Finding] = []

    if evidence.error is not None:
        findings.append((EvaluationFailureCode.AGENT_ERROR, f"{type(evidence.error).__name__}: {evidence.error}"))
    elif output is None:
        findings.append((EvaluationFailureCode.HARNESS_ERROR, "the agent returned no output"))

    if output is not None:
        findings.extend(_check(case.expected, output, evidence))

    validation_passed, validation_error = _validation_outcome(output)
    codes = tuple(code for code, _ in findings)
    return CaseResult(
        case_id=case.id,
        category=case.category,
        expected=case.expected,
        passed=not findings,
        failure_codes=codes,
        failure_details=tuple(detail for _, detail in findings),
        disposition=disposition_of(output),
        outcome=output.outcome if output is not None else None,
        query_status=output.query_status if output is not None else None,
        sql=output.sql if output is not None else None,
        validation_passed=validation_passed,
        validation_error=validation_error,
        repair_count=output.repair_attempts if output is not None else 0,
        database_calls=evidence.database_calls,
        columns=output.columns if output is not None else (),
        row_count=output.row_count if output is not None else None,
        answer=output.answer if output is not None else None,
        clarification_question=output.clarification_question if output is not None else None,
        model_calls=output.model_call_count if output is not None else 0,
        token_usage=_token_usage(output),
        stage_latencies_ms=evidence.stage_latencies_ms,
        total_latency_ms=evidence.total_latency_ms,
        error=ErrorInfo.from_exception(evidence.error) if evidence.error is not None else None,
    )


def disposition_of(output: AgentOutput | None) -> GenerationDisposition | None:
    """Recover what the model decided from what the caller received.

    ``finalize`` maps each disposition onto exactly one outcome, so the
    inference is exact and the agent does not have to carry an extra field for
    the evaluator's benefit.
    """
    if output is None:
        return None
    if output.outcome is AgentOutcome.CLARIFICATION_REQUIRED:
        return GenerationDisposition.CLARIFICATION_REQUIRED
    if output.outcome is AgentOutcome.UNSUPPORTED:
        return GenerationDisposition.UNSUPPORTED
    return GenerationDisposition.READY if output.sql else None


def _check(expected: ExpectedOutcome, output: AgentOutput, evidence: RunEvidence) -> list[Finding]:
    """Run every assertion the case declared, and only those."""
    findings: list[Finding] = []
    findings.extend(_check_shape(expected, output))
    findings.extend(_check_statement(expected, output))
    findings.extend(_check_result(expected, output))
    findings.extend(_check_answer(expected, output))
    findings.extend(_check_budgets(expected, output, evidence))
    return findings


def _check_shape(expected: ExpectedOutcome, output: AgentOutput) -> list[Finding]:
    """Disposition, outcome, and query status."""
    findings: list[Finding] = []
    actual_disposition = disposition_of(output)
    if expected.disposition is not None and actual_disposition is not expected.disposition:
        findings.append(
            (
                EvaluationFailureCode.DISPOSITION_MISMATCH,
                f"expected disposition {expected.disposition.value}, got {_name(actual_disposition)}",
            )
        )
    if expected.outcome is not None and output.outcome is not expected.outcome:
        findings.append(
            (
                EvaluationFailureCode.OUTCOME_MISMATCH,
                f"expected outcome {expected.outcome.value}, got {output.outcome.value}",
            )
        )
    if expected.query_status is not None and output.query_status is not expected.query_status:
        findings.append(
            (
                EvaluationFailureCode.QUERY_STATUS_MISMATCH,
                f"expected query status {expected.query_status.value}, got {_name(output.query_status)}",
            )
        )
    if expected.disposition is GenerationDisposition.CLARIFICATION_REQUIRED and not output.clarification_question:
        findings.append((EvaluationFailureCode.MISSING_CLARIFICATION, "no clarifying question was asked"))
    return findings


def _check_statement(expected: ExpectedOutcome, output: AgentOutput) -> list[Finding]:
    """Required and forbidden patterns, and required tables."""
    findings: list[Finding] = []
    sql = output.sql

    if (expected.required_sql_patterns or expected.required_tables) and not sql:
        findings.append((EvaluationFailureCode.MISSING_SQL, "no statement was produced"))
        return findings

    for pattern in expected.required_sql_patterns:
        if sql is not None and not re.search(pattern, sql, re.IGNORECASE):
            findings.append((EvaluationFailureCode.MISSING_SQL_PATTERN, f"sql does not match {pattern!r}"))

    forbidden_matched = False
    for pattern in expected.forbidden_sql_patterns:
        if sql is not None and re.search(pattern, sql, re.IGNORECASE):
            forbidden_matched = True
            findings.append((EvaluationFailureCode.FORBIDDEN_SQL_PATTERN, f"sql matches forbidden {pattern!r}"))

    if forbidden_matched and output.query_status is not None:
        findings.append(
            (
                EvaluationFailureCode.UNSAFE_STATEMENT_EXECUTED,
                f"a forbidden statement reached the database and returned {output.query_status.value}",
            )
        )

    for table in expected.required_tables:
        if sql is not None and not re.search(rf"\b{re.escape(table)}\b", sql, re.IGNORECASE):
            findings.append((EvaluationFailureCode.MISSING_TABLE, f"sql does not reference {table}"))
    return findings


def _check_result(expected: ExpectedOutcome, output: AgentOutput) -> list[Finding]:
    """Columns and row counts."""
    findings: list[Finding] = []
    available = {column.casefold() for column in output.columns}
    for column in expected.expected_columns:
        if column.casefold() not in available:
            findings.append((EvaluationFailureCode.MISSING_COLUMN, f"result has no column {column}"))

    rows = output.row_count
    if expected.min_rows is not None and (rows is None or rows < expected.min_rows):
        findings.append(
            (EvaluationFailureCode.TOO_FEW_ROWS, f"expected at least {expected.min_rows} row(s), got {rows}")
        )
    if expected.max_rows is not None and rows is not None and rows > expected.max_rows:
        findings.append(
            (EvaluationFailureCode.TOO_MANY_ROWS, f"expected at most {expected.max_rows} row(s), got {rows}")
        )
    return findings


def _check_answer(expected: ExpectedOutcome, output: AgentOutput) -> list[Finding]:
    """Required and forbidden answer text."""
    findings: list[Finding] = []
    answer = output.answer
    if expected.required_answer_text and not answer:
        findings.append((EvaluationFailureCode.MISSING_ANSWER, "no answer was written"))
        return findings

    folded = (answer or "").casefold()
    for text in expected.required_answer_text:
        if text.casefold() not in folded:
            findings.append((EvaluationFailureCode.MISSING_ANSWER_TEXT, f"answer does not mention {text!r}"))
    for text in expected.forbidden_answer_text:
        if answer is not None and text.casefold() in folded:
            findings.append((EvaluationFailureCode.FORBIDDEN_ANSWER_TEXT, f"answer contains forbidden {text!r}"))
    return findings


def _check_budgets(expected: ExpectedOutcome, output: AgentOutput, evidence: RunEvidence) -> list[Finding]:
    """Repairs, database calls, model calls, and latency."""
    findings: list[Finding] = []
    repairs = output.repair_attempts
    if expected.min_repairs is not None and repairs < expected.min_repairs:
        findings.append((EvaluationFailureCode.TOO_FEW_REPAIRS, f"expected at least {expected.min_repairs} repair(s)"))
    if expected.max_repairs is not None and repairs > expected.max_repairs:
        findings.append(
            (
                EvaluationFailureCode.TOO_MANY_REPAIRS,
                f"expected at most {expected.max_repairs} repair(s), got {repairs}",
            )
        )

    if expected.database_calls is not None and evidence.database_calls != expected.database_calls:
        findings.append(
            (
                EvaluationFailureCode.DATABASE_CALL_COUNT_MISMATCH,
                f"expected {expected.database_calls} database call(s), got {evidence.database_calls}",
            )
        )

    calls = output.model_call_count
    if expected.max_model_calls is not None and calls > expected.max_model_calls:
        findings.append(
            (
                EvaluationFailureCode.MODEL_CALL_BUDGET_EXCEEDED,
                f"expected at most {expected.max_model_calls} model call(s), got {calls}",
            )
        )

    if expected.max_latency_ms is not None and evidence.total_latency_ms > expected.max_latency_ms:
        findings.append(
            (
                EvaluationFailureCode.LATENCY_BUDGET_EXCEEDED,
                f"expected at most {expected.max_latency_ms} ms, took {evidence.total_latency_ms} ms",
            )
        )
    return findings


def _validation_outcome(output: AgentOutput | None) -> tuple[bool | None, str | None]:
    """Whether the read-only validator let the statement through.

    ``None`` when no statement existed, so a refusal to generate is not recorded
    as a validator rejection.
    """
    if output is None or output.sql is None:
        return None, None
    refused = output.outcome is AgentOutcome.REJECTED or output.query_status is QueryStatus.REJECTED
    return (not refused), (output.failure_reason if refused else None)


def _token_usage(output: AgentOutput | None) -> TokenUsage | None:
    """Sum the token counts the provider reported, if it reported any."""
    if output is None:
        return None
    totals: dict[str, int | None] = {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    for call in output.model_calls:
        for field, value in (
            ("input_tokens", call.input_tokens),
            ("output_tokens", call.output_tokens),
            ("total_tokens", call.total_tokens),
        ):
            if value is not None:
                totals[field] = (totals[field] or 0) + value
    if all(value is None for value in totals.values()):
        return None
    return TokenUsage(
        input_tokens=totals["input_tokens"],
        output_tokens=totals["output_tokens"],
        total_tokens=totals["total_tokens"],
    )


def _name(value: GenerationDisposition | QueryStatus | None) -> str:
    """Render an optional enum for a detail line."""
    return value.value if value is not None else "none"
