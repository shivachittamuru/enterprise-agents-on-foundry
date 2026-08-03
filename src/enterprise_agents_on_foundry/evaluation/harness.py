"""Running one case, in either execution mode.

The runner does not reimplement the graph. It calls the same public entry point
a notebook calls, :func:`answer_question`, and differs only in which
:class:`AgentDependencies` it hands over.

Two modes exist because two questions are being asked. ``live`` measures the
configured Foundry deployment against Azure SQL. ``deterministic`` replays
scripted responses through the real graph, so the evaluator, the metrics, and
the exit code can be exercised in a unit test with no cloud, no model, and no
driver. Both paths run the same nodes, the same routing, and the same read-only
validator.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from enterprise_agents_on_foundry.agents.model import ModelInvocation
from enterprise_agents_on_foundry.agents.nodes import AgentDependencies, answer_question, production_dependencies
from enterprise_agents_on_foundry.agents.state import (
    AgentInput,
    AgentOutput,
    GenerationDisposition,
    ModelCallMetadata,
    SqlGenerationResult,
)
from enterprise_agents_on_foundry.config.settings import Settings
from enterprise_agents_on_foundry.database.connection import DatabaseClient
from enterprise_agents_on_foundry.database.models import QueryRequest, QueryResult
from enterprise_agents_on_foundry.database.tool import QueryStatus, QueryTool, QueryToolResult
from enterprise_agents_on_foundry.errors import EvaluationDatasetError
from enterprise_agents_on_foundry.evaluation.cases import EvaluationCase
from enterprise_agents_on_foundry.evaluation.dataset import read_json_lines
from enterprise_agents_on_foundry.observability.timing import Stopwatch

DETERMINISTIC_SCHEMA: Final = "SalesLT.Product\n  Name nvarchar not null\n  ListPrice money null"
"""A schema stub. The scripted model already knows what it will answer."""

DETERMINISTIC_DEPLOYMENT: Final = "deterministic-fake"
"""Recorded on every scripted model call, so evidence never claims a real deployment."""

DependencyFactory = Callable[[EvaluationCase], AgentDependencies]
"""Builds the dependencies for one case. Its query tool must be a :class:`CountingQueryTool`."""

__all__ = [
    "DETERMINISTIC_DEPLOYMENT",
    "DETERMINISTIC_SCHEMA",
    "CountingQueryTool",
    "DependencyFactory",
    "ExecutionMode",
    "RunEvidence",
    "ScriptedResponse",
    "deterministic_dependencies",
    "live_dependencies",
    "load_scripted_responses",
    "require_scripts",
    "run_case",
]


class ExecutionMode(StrEnum):
    """Where the answers come from."""

    DETERMINISTIC = "deterministic"
    LIVE = "live"


@dataclass(slots=True)
class CountingQueryTool:
    """Counts query-tool calls without changing what the tool does.

    The agent does not report how many times it reached the database, and adding
    a counter to its state would make the graph carry an evaluation concern. The
    boundary is wrapped instead, so the count is observed from outside.
    """

    inner: QueryTool
    calls: int = 0
    elapsed_ms: float = 0.0

    def execute(self, request: QueryRequest) -> QueryToolResult:
        """Run the wrapped tool and record that it ran."""
        self.calls += 1
        outcome = self.inner.execute(request)
        self.elapsed_ms += outcome.elapsed_ms
        return outcome


@dataclass(frozen=True, slots=True)
class RunEvidence:
    """What one execution did, before anything is graded."""

    output: AgentOutput | None
    database_calls: int
    total_latency_ms: float
    stage_latencies_ms: dict[str, float]
    error: Exception | None = None


def run_case(case: EvaluationCase, make_dependencies: DependencyFactory) -> RunEvidence:
    """Run one case and capture the evidence, whatever happened.

    An exception is evidence rather than a reason to stop: one broken case must
    not hide the results of the twenty-eight that follow it. Only a failure to
    build the dependencies at all, which happens once and before the run,
    reaches the caller.
    """
    dependencies = make_dependencies(case)
    tool = dependencies.query_tool
    counter = tool if isinstance(tool, CountingQueryTool) else None

    stopwatch = Stopwatch()
    output: AgentOutput | None = None
    error: Exception | None = None
    try:
        output = answer_question(dependencies, AgentInput(question=case.question))
    except Exception as caught:  # an agent failure is one case result, not a crashed run
        error = caught
    elapsed = stopwatch.stop()

    database_ms = counter.elapsed_ms if counter is not None else 0.0
    return RunEvidence(
        output=output,
        database_calls=counter.calls if counter is not None else 0,
        total_latency_ms=elapsed,
        stage_latencies_ms=_stage_latencies(output, database_ms),
        error=error,
    )


def live_dependencies(settings: Settings, client: DatabaseClient) -> DependencyFactory:
    """Wire the configured Foundry deployment and Azure SQL, counted per case.

    The model client and the connection are built once and shared; only the
    counter is per case, so evaluating a dataset does not open a connection per
    question. Microsoft Entra authentication is untouched: this adds a wrapper
    around the same read-only tool the agent already uses.
    """
    base = production_dependencies(settings, client)

    def make(case: EvaluationCase) -> AgentDependencies:
        return replace(base, query_tool=CountingQueryTool(base.query_tool))

    return make


class ScriptedResponse(BaseModel):
    """One recorded agent behaviour, replayed through the real graph."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    disposition: GenerationDisposition
    sql: str | None = None
    rationale: str = ""
    clarification_question: str | None = None
    status: QueryStatus | None = Field(default=None, description="What the query tool reports back.")
    columns: tuple[str, ...] = ()
    rows: int = Field(default=0, ge=0, description="How many synthesized rows the tool returns.")
    error: str | None = Field(default=None, description="Required when the tool rejects or fails.")
    answer: str = ""

    @model_validator(mode="after")
    def _script_is_runnable(self) -> ScriptedResponse:
        """Reject a script the graph could not have produced."""
        if self.disposition is GenerationDisposition.READY:
            if not self.sql:
                raise ValueError("sql is required when disposition is ready")
            if self.status is None:
                raise ValueError("status is required when disposition is ready")
        elif self.sql or self.status is not None:
            raise ValueError(f"a {self.disposition.value} script cannot carry sql or a query status")

        if self.status in {QueryStatus.SUCCESS, QueryStatus.EMPTY}:
            if not self.columns:
                raise ValueError("columns are required when the query returns a result")
            if (self.status is QueryStatus.EMPTY) != (self.rows == 0):
                raise ValueError("rows must match the query status")
        elif self.status is not None and not self.error:
            raise ValueError("error is required when the query is rejected or fails")
        return self

    def generation(self) -> SqlGenerationResult:
        """The value the scripted model returns to ``generate_sql``."""
        return SqlGenerationResult(
            disposition=self.disposition,
            sql=self.sql,
            rationale=self.rationale,
            clarification_question=self.clarification_question,
        )

    def tool_result(self, label: str | None) -> QueryToolResult:
        """The value the scripted tool returns to ``execute_sql``."""
        if self.status in {QueryStatus.SUCCESS, QueryStatus.EMPTY}:
            rows = tuple(tuple(f"{column}-{index}" for column in self.columns) for index in range(self.rows))
            result = QueryResult(columns=self.columns, rows=rows, truncated=False, elapsed_ms=0.0, label=label)
            return QueryToolResult(status=self.status, result=result)
        return QueryToolResult(status=self.status or QueryStatus.FAILED, error=self.error or "The query did not run.")

    def prose(self) -> str:
        """The value the scripted model returns to ``compose_answer``."""
        return self.answer or f"The query returned {self.rows} row(s)."


def load_scripted_responses(path: Path) -> dict[str, ScriptedResponse]:
    """Read the scripted responses that drive deterministic mode."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise EvaluationDatasetError(f"{path}: cannot be read: {error}") from error

    scripts: dict[str, ScriptedResponse] = {}
    for number, payload in read_json_lines(text.splitlines(), source=str(path)):
        script = _validated_script(payload, source=str(path), number=number)
        if script.id in scripts:
            raise EvaluationDatasetError(f"{path}:{number}: duplicate script id {script.id!r}")
        scripts[script.id] = script
    return scripts


def require_scripts(cases: tuple[EvaluationCase, ...], scripts: Mapping[str, ScriptedResponse]) -> None:
    """Refuse to start a deterministic run that would silently skip cases."""
    missing = sorted(case.id for case in cases if case.id not in scripts)
    if missing:
        raise EvaluationDatasetError(f"no deterministic script for: {', '.join(missing)}")


def deterministic_dependencies(scripts: Mapping[str, ScriptedResponse]) -> DependencyFactory:
    """Replay scripted responses through the real nodes, routing, and validator."""

    def make(case: EvaluationCase) -> AgentDependencies:
        script = scripts.get(case.id)
        if script is None:
            raise EvaluationDatasetError(f"no deterministic script for case {case.id!r}")
        return AgentDependencies(
            load_schema=lambda: DETERMINISTIC_SCHEMA,
            draft_sql=lambda purpose, system, user: ModelInvocation(
                metadata=_scripted_metadata(purpose),
                value=script.generation(),
            ),
            write_answer=lambda purpose, system, user: ModelInvocation(
                metadata=_scripted_metadata(purpose),
                value=script.prose(),
            ),
            query_tool=CountingQueryTool(_ScriptedQueryTool(script)),
        )

    return make


@dataclass(frozen=True, slots=True)
class _ScriptedQueryTool:
    """Returns the recorded outcome for whatever statement it is handed."""

    script: ScriptedResponse

    def execute(self, request: QueryRequest) -> QueryToolResult:
        """Replay the recorded database outcome."""
        return self.script.tool_result(request.label)


def _validated_script(payload: dict[str, Any], *, source: str, number: int) -> ScriptedResponse:
    """Validate one script, naming the line that is wrong."""
    try:
        return ScriptedResponse.model_validate(payload)
    except ValueError as error:
        raise EvaluationDatasetError(f"{source}:{number}: invalid script: {error}") from error


def _scripted_metadata(purpose: str) -> ModelCallMetadata:
    """Metadata for a call that cost nothing and reached no deployment."""
    return ModelCallMetadata(purpose=purpose, deployment=DETERMINISTIC_DEPLOYMENT)


def _stage_latencies(output: AgentOutput | None, database_ms: float) -> dict[str, float]:
    """Attribute elapsed time to the boundaries that reported it.

    Model calls report their own duration and the wrapped tool accumulates the
    database time. Anything the graph spent elsewhere stays in the total rather
    than being invented as a stage.
    """
    stages: dict[str, float] = {}
    for call in output.model_calls if output is not None else ():
        if call.latency_ms is not None:
            stages[call.purpose] = round(stages.get(call.purpose, 0.0) + call.latency_ms, 1)
    if database_ms:
        stages["execute_sql"] = round(database_ms, 1)
    return stages
