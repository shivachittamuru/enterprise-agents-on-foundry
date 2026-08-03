"""``eaof-evaluate``: grade the Text-to-SQL agent against the evaluation dataset.

Two modes answer two questions. ``deterministic`` replays scripted responses
through the real graph and needs no cloud, no model, and no driver, so the
evaluator itself can be checked. ``live`` runs the configured Foundry deployment
against Azure SQL over Microsoft Entra authentication.

Exit codes:

* ``0`` every selected case met its expectation
* ``1`` the run completed and at least one case failed
* ``2`` the run could not start: dataset, scripts, configuration, or credential
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from enterprise_agents_on_foundry.cli.console import EXIT_FAILED, EXIT_OK, EXIT_UNAVAILABLE, section
from enterprise_agents_on_foundry.config.settings import load_settings, repository_root
from enterprise_agents_on_foundry.database.connection import connect
from enterprise_agents_on_foundry.errors import (
    ConfigurationError,
    DatabaseConnectionError,
    EvaluationDatasetError,
    UnsafeDatabaseTargetError,
)
from enterprise_agents_on_foundry.evaluation.cases import EvaluationCase, EvaluationCategory
from enterprise_agents_on_foundry.evaluation.dataset import filter_cases, load_cases
from enterprise_agents_on_foundry.evaluation.harness import (
    ExecutionMode,
    deterministic_dependencies,
    load_scripted_responses,
    require_scripts,
)
from enterprise_agents_on_foundry.evaluation.harness import live_dependencies as build_live_dependencies
from enterprise_agents_on_foundry.evaluation.metrics import RunSummary
from enterprise_agents_on_foundry.evaluation.runner import EvaluationRun, run_dataset, write_run

DEFAULT_DATASET = Path("evals/datasets/text-to-sql.jsonl")
DEFAULT_SCRIPTS = Path("evals/fixtures/deterministic-agent.jsonl")
DEFAULT_OUTPUT = Path("artifacts/evaluations")
"""Runs land in an ignored directory: per-case evidence quotes rows and prose."""

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    """Run the selected cases and return a process exit code."""
    args = _parser().parse_args(argv)
    root = repository_root()

    try:
        cases = _selected_cases(args, root)
    except EvaluationDatasetError as error:
        print(error)
        return EXIT_UNAVAILABLE
    if not cases:
        print("No cases matched the selection.")
        return EXIT_UNAVAILABLE

    section("Selection")
    print(f"  Mode:    {args.mode}")
    print(f"  Dataset: {args.dataset}")
    print(f"  Cases:   {len(cases)}")

    if args.mode == ExecutionMode.DETERMINISTIC:
        outcome = _run_deterministic(cases, _resolve(args.scripts, root))
    else:
        outcome = _run_live(cases)
    if isinstance(outcome, int):
        return outcome

    output_dir = _resolve(args.output_dir, root)
    results_path, summary_path = write_run(outcome, output_dir)
    _report(outcome, results_path, summary_path)
    return EXIT_OK if outcome.all_passed else EXIT_FAILED


def _parser() -> argparse.ArgumentParser:
    """Describe the command line."""
    parser = argparse.ArgumentParser(prog="eaof-evaluate", description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET, help="JSONL dataset to run.")
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in ExecutionMode],
        default=ExecutionMode.DETERMINISTIC.value,
        help="Where the answers come from.",
    )
    parser.add_argument(
        "--scripts", type=Path, default=DEFAULT_SCRIPTS, help="Scripted responses for deterministic mode."
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT, help="Where results and the summary are written."
    )
    parser.add_argument("--case", action="append", dest="cases", metavar="ID", help="Run only this case. Repeatable.")
    parser.add_argument(
        "--category",
        action="append",
        dest="categories",
        choices=[category.value for category in EvaluationCategory],
        help="Run only this category. Repeatable.",
    )
    parser.add_argument(
        "--tag", action="append", dest="tags", metavar="TAG", help="Run only cases with this tag. Repeatable."
    )
    return parser


def _selected_cases(args: argparse.Namespace, root: Path) -> tuple[EvaluationCase, ...]:
    """Load the dataset and apply the requested filters."""
    cases = load_cases(_resolve(args.dataset, root))
    categories = [EvaluationCategory(value) for value in args.categories] if args.categories else None
    return filter_cases(cases, case_ids=args.cases, categories=categories, tags=args.tags)


def _run_deterministic(cases: tuple[EvaluationCase, ...], scripts_path: Path) -> EvaluationRun | int:
    """Replay scripted responses, or explain why the scripts cannot be used."""
    try:
        scripts = load_scripted_responses(scripts_path)
        require_scripts(cases, scripts)
    except EvaluationDatasetError as error:
        print(error)
        return EXIT_UNAVAILABLE
    return run_dataset(cases, deterministic_dependencies(scripts))


def _run_live(cases: tuple[EvaluationCase, ...]) -> EvaluationRun | int:
    """Run against the configured deployment and database.

    Configuration and connection failures happen once, before any case runs, and
    end the command. A per-case failure is a result, not an outage.
    """
    try:
        settings = load_settings()
        client = connect(settings)
    except (ConfigurationError, UnsafeDatabaseTargetError, DatabaseConnectionError) as error:
        section("Result: UNAVAILABLE")
        print(f"  {error}")
        return EXIT_UNAVAILABLE

    with client:
        return run_dataset(cases, build_live_dependencies(settings, client))


def _report(run: EvaluationRun, results_path: Path, summary_path: Path) -> None:
    """Print the scorecard and where the evidence went."""
    summary = run.summary
    section("Verdict")
    print(f"  {summary.passed}/{summary.total} passed ({summary.pass_rate:.0%})")

    section("Quality and safety")
    for label, value in (
        ("Unsafe rejection rate", summary.unsafe_rejection_rate),
        ("Safe acceptance rate", summary.safe_acceptance_rate),
        ("Repair success rate", summary.repair_success_rate),
        ("Unexpected exception rate", summary.unexpected_exception_rate),
    ):
        print(f"  {label:<26} {_ratio(value)}")

    section("Performance and economics")
    print(f"  {'Median latency':<26} {_number(summary.median_latency_ms)} ms")
    print(f"  {'P95 latency':<26} {_number(summary.p95_latency_ms)} ms")
    print(f"  {'Mean model calls':<26} {_number(summary.mean_model_calls)}")
    print(f"  {'Max model calls':<26} {_number(summary.max_model_calls)}")
    print(f"  {'Total tokens':<26} {_number(summary.total_tokens)}")

    section("By category")
    for name, metrics in summary.by_category.items():
        print(f"  {name:<20} {metrics.passed}/{metrics.total}")

    _report_failures(summary)

    section("Evidence")
    print(f"  {results_path}")
    print(f"  {summary_path}")


def _report_failures(summary: RunSummary) -> None:
    """List the failure codes that were raised, most frequent first."""
    if not summary.failure_counts:
        return
    section("Failures by code")
    for code, count in summary.failure_counts.items():
        print(f"  {code:<34} {count}")


def _resolve(path: Path, root: Path) -> Path:
    """Interpret a relative path against the repository root."""
    return path if path.is_absolute() else root / path


def _ratio(value: float | None) -> str:
    """Render an optional rate as a percentage."""
    return f"{value:.0%}" if value is not None else "not measured"


def _number(value: float | int | None) -> str:
    """Render an optional number."""
    return "not measured" if value is None else f"{value}"


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
