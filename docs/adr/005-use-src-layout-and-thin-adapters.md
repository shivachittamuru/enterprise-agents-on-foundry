---
title: "ADR 005: Use a src Layout with Named Boundaries and Thin Adapters"
description: Decision to replace the single setup package with five named boundaries under a src layout, and to reduce scripts and notebooks to adapters that import package code.
author: shiva
ms.date: 2026-07-28
ms.topic: concept
keywords:
  - adr
  - python
  - packaging
  - testing
estimated_reading_time: 7
---

## Status

Accepted, 2026-07-28.

## Context

v0.1 put every reusable implementation in `src/enterprise_agents_on_foundry/setup/`.
That was the right call for one release: it kept the code out of the notebook and
made it testable. It does not survive a second release.

The name is the problem. `setup` describes when the code runs, not what it is
responsible for, so every new capability has an equally good claim to belong
there. By the end of v0.1 the package held six modules covering configuration,
subprocess execution, Azure CLI output parsing, model catalog queries, SQL
validation, ODBC connection construction, and timing. Nothing was misplaced,
because a package named after a phase has no boundary to be outside of.

Three concrete symptoms had already appeared.

Three near-identical subprocess wrappers existed, one each in the prerequisite
check, the Azure account lookup, and the model catalog query. Each built a
command, ran it, and interpreted a failure slightly differently.

The same header-printing helper was defined twice, once in each script.

`scripts/preprovision_check.py` read four values with `os.environ.get` and
supplied inline defaults, so the model SKU and capacity were declared in
`Settings` and defaulted somewhere else. A change to one did not reach the other.

v0.3 migrates a LangGraph agent onto this codebase. An agent needs configuration,
a database client, schema discovery, error types, and telemetry, and it will
acquire whichever shape it finds. Deciding the boundaries after the agent arrives
means changing the agent to fit them.

## Decision

Keep the `src/` layout and give the package five named boundaries.

The `src/` layout stays because it makes the import path honest. The source
directory is not on `sys.path`, so the only way to import the package is to
install it, which `uv sync` does in editable mode. What the tests import is
therefore what the wheel contains. Under a flat layout, a module missing from the
build or a package that was never installed stays invisible until someone
installs the artifact elsewhere.

Replace `setup` with boundaries named for responsibility:

| Boundary         | Responsibility                                                     |
|------------------|--------------------------------------------------------------------|
| `config`         | Load and validate every setting, once, from one entry point         |
| `infrastructure` | Everything that shells out: tools, Azure CLI, azd, model catalog     |
| `database`       | Target validation, read-only SQL rules, connection, schema metadata |
| `observability`  | Timing and release measurements                                     |
| `cli`            | Presentation and exit codes for the packaged commands                |

Add `errors.py` at the package root with `EaofError` as the base for every error
the project raises, and five subclasses that name the five distinct situations.
This replaces a mixture of custom types, bare `ValueError`, and `PermissionError`.
A caller can now catch everything the project raises without also catching bugs,
and the choice of subclass determines the exit code a command returns.

Reduce scripts to adapters. A script parses arguments, calls package functions,
prints for a human, and returns an exit code. It holds no rule. Two tests enforce
this: one caps script length, and one asserts that no script reads an environment
variable directly.

Reduce notebooks to consumers. A notebook narrates and imports. It never defines
a second implementation of something the package already provides, because that
second implementation is untested and drifts on the first change to the first.

Split the test suite by what it needs rather than by what it covers.
`tests/unit/` requires nothing but Python and runs in seconds. `tests/integration/`
is marked `azure` and skips with a reason when the environment is absent.

Expose two console scripts, `eaof-verify` and `eaof-db-info`, through
`[project.scripts]`. Both return `0` for pass, `1` for ran and failed, and `2` for
could not run.

## Consequences

Positive.

A new capability has an obvious home, or it reveals that a boundary is missing.
Either outcome is a decision rather than an accident.

The offline suite runs on every save. Parsing, validation, comparison, row
limits, and exit codes are pure functions specifically so they can be decided
without a subscription, which is what stops the suite from being skipped.

The v0.3 agent inherits a database client, schema discovery, typed errors, and a
measurement mechanism instead of inventing four more.

Exit code `2` distinguishes a missing ODBC driver from a broken database. A tool
that returns `1` for both sends people to debug the wrong system.

Negative.

Import statements are longer. `from enterprise_agents_on_foundry.setup import X`
became a path that names the boundary. That verbosity is the point, but it is
still verbosity, and the top-level `__init__.py` deliberately does not re-export
subpackage names to shorten it, because re-exporting would erase the boundary it
was created to make visible.

Five directories replaced one, so the package looks larger for the same amount of
behaviour.

The v0.1 notebook needed its imports updated, and no compatibility shim was left
behind. A shim would have been two lines and would have kept the old name alive
in exactly the way this decision exists to prevent.

The `DatabaseClient.raw_connection` property is a deliberate hole in the
read-only boundary, used by one caller: the administrative grant in
`scripts/bootstrap_database.py`, which must run a statement that is not a
`SELECT`. It is named and documented rather than hidden so that any future use is
an obvious review question.

## Alternatives considered

Keep `setup` and add modules to it. Rejected. The problem is the name, and adding
to it accelerates the symptom.

Split by layer instead of by responsibility, with `models`, `services`, and
`clients`. Rejected. Layer names describe shape rather than subject, so deciding
where something belongs requires knowing the convention rather than the domain.

Move to a flat layout to shorten imports. Rejected. It trades a permanent
correctness property for typing convenience.

Keep one test directory and rely on skip conditions. Rejected. A suite that needs
a subscription to be meaningful is a suite that stops being run. The marker split
makes the fast path the default path.

Publish a compatibility shim re-exporting the old names. Rejected. v0.1 is a
tagged release, and this repository is a curriculum where the rename is part of
the lesson.

## Related

* [ADR 001: Create a New Repository](001-create-new-repository.md)
* [ADR 003: Use the Azure Developer CLI with Modular Bicep](003-use-azd-and-modular-bicep.md)
* [v0.2 Python Foundation](../architecture/v0.2-python-foundation.md)
* [Release v0.2](../releases/v0.2.md)
