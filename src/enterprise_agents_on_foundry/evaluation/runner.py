"""Running a dataset and writing the evidence out.

The runner owns sequencing and persistence only. It loads cases, runs each one
through the harness, grades it, aggregates the verdicts, and writes two files.
It contains no routing, no prompt, and no SQL, so the thing being measured and
the thing doing the measuring cannot drift apart.

Ordinary case failures do not stop a run. A failure to load the dataset or to
build the dependencies does, because a partial run reported as a full one would
overstate the score.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from enterprise_agents_on_foundry.evaluation.cases import EvaluationCase
from enterprise_agents_on_foundry.evaluation.checks import grade
from enterprise_agents_on_foundry.evaluation.harness import DependencyFactory, run_case
from enterprise_agents_on_foundry.evaluation.metrics import RunSummary, summarize
from enterprise_agents_on_foundry.evaluation.results import CaseResult

RESULTS_FILENAME = "results.jsonl"
SUMMARY_FILENAME = "summary.json"

__all__ = [
    "RESULTS_FILENAME",
    "SUMMARY_FILENAME",
    "EvaluationRun",
    "run_dataset",
    "write_run",
]


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    """Every graded case and the scorecard built from them."""

    results: tuple[CaseResult, ...]
    summary: RunSummary

    @property
    def all_passed(self) -> bool:
        """True when every case that ran met its expectation."""
        return self.summary.failed == 0


def run_dataset(cases: tuple[EvaluationCase, ...], make_dependencies: DependencyFactory) -> EvaluationRun:
    """Run and grade every case, in dataset order."""
    results = tuple(grade(case, run_case(case, make_dependencies)) for case in cases)
    return EvaluationRun(results=results, summary=summarize(results))


def write_run(run: EvaluationRun, output_dir: Path) -> tuple[Path, Path]:
    """Write one result per line and one summary, and return both paths.

    Case order follows the dataset and every field is serialized by the model
    itself, so two runs of the same evidence produce byte-identical files apart
    from the measured durations.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / RESULTS_FILENAME
    summary_path = output_dir / SUMMARY_FILENAME

    lines = [result.model_dump_json() for result in run.results]
    results_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    summary_path.write_text(run.summary.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return results_path, summary_path
