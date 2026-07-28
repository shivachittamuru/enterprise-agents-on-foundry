---
title: "ADR 001: Create a New Repository Instead of Refactoring in Place"
description: Decision to build the modernized curriculum in a new repository, keeping the brownfield backend intact as a read-only reference.
author: shiva
ms.date: 2026-07-27
ms.topic: concept
keywords:
  - adr
  - repository
estimated_reading_time: 4
---

## Status

Accepted, 2026-07-27.

## Context

The brownfield project at `../langgraph-agents-on-azure` contains a working
LangGraph Text-to-SQL agent, a FastAPI service, six teaching notebooks, a
frontend, and CI workflows. It also carries the twelve risks catalogued in
[modernization-risks.md](../current-state/modernization-risks.md).

The curriculum being built is progressive. Each release adds one concept and
leaves a readable checkpoint behind. That structure only works if the history
reads as a deliberate sequence.

Refactoring in place was considered. Three properties made it unattractive.

The first release changes the substrate rather than the agent: dependency
management, database, identity model, and infrastructure lifecycle all change at
once. Applied in place, that is a single commit touching nearly every file,
which is the opposite of a readable checkpoint.

The legacy repository is also a teaching artifact. Its defects are the material
for the first lesson. Fixing them in place destroys the before state that the
lesson needs.

The legacy repository additionally contains a frontend, CI workflows targeting
AKS, and roughly 5 MB of committed database binaries. Removing those from
history is disruptive. Leaving them makes every clone carry them.

## Decision

Build the modernized curriculum in a new repository,
`shivachittamuru/enterprise-agents-on-foundry`. Treat
`../langgraph-agents-on-azure` as a read-only reference and never modify it.

Every claim made about the legacy implementation cites a specific path under
`../langgraph-agents-on-azure/backend`, so a reader can verify it.

## Consequences

The new repository starts with a Python 3.12 and uv baseline, four runtime
dependencies, and no inherited history.

Each release is a coherent, reviewable increment, and readers can check out an
earlier tag to see the state at that point.

The before and after comparison in
[../releases/v0.1.md](../releases/v0.1.md) is measurable, because both states
exist simultaneously.

Reusable material has to be carried across deliberately rather than inherited.
The `api/schema.py` message boundary, the root-span tracing concept, the
progressive notebook format, and the evaluation dataset shape are the items
identified for reuse.

The two repositories will coexist while the curriculum is written, which means
keeping the reference checkout available. That is the accepted cost.

## Related

- [002-use-azure-sql-adventureworks-lt.md](002-use-azure-sql-adventureworks-lt.md)
- [003-use-azd-and-modular-bicep.md](003-use-azd-and-modular-bicep.md)
