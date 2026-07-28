---
title: Roadmap
description: Planned progression of releases from the brownfield baseline through the Python foundation, modern LangGraph, Microsoft Foundry, observability, performance, governance, and distribution.
author: shiva
ms.date: 2026-07-28
ms.topic: concept
keywords:
  - roadmap
  - curriculum
estimated_reading_time: 7
---

## Shape of the curriculum

Each release adds one concept, leaves a readable checkpoint, and keeps the
Text-to-SQL agent deliberately understandable. The subject is the platform and
the architecture around the agent, so agent complexity is added only when a
lesson requires it.

Version numbers below the first are planned rather than committed. Scope may
move between releases as the material develops.

## v0.1: Brownfield baseline, repository bootstrap, and Azure foundation

Shipped. Analyse the legacy backend, establish the modern repository, and
provision the Azure foundation. See [releases/v0.1.md](releases/v0.1.md).

## v0.2: Modern Python foundation and clean package layout

Establish the typed, testable application foundation the agent will be built on.
One configuration boundary, one database boundary, reusable Azure environment
helpers, thin script and CLI adapters, and notebooks that import production code
rather than redefining it. No agent code and no LangGraph.

## v0.3: Modern LangGraph architecture

Rebuild the Text-to-SQL agent on LangGraph 1.2 using typed state, explicit
routing, bounded retries, token-aware context management, and durable
checkpointing. Evaluate current prebuilt patterns, but use them only where they
improve clarity and control: for Text-to-SQL an explicit custom graph may remain
clearer and safer than a prebuilt agent. Replace the fabricated tool call
identifier and the six-message rolling window. Introduce durable checkpointing
using a supported persistent store, with Azure SQL evaluated as one option. One
implementation, no duplicate PostgreSQL variant.

## v0.4: Models and tools

Model selection and configuration as an explicit concern. Structured output.
Tool design, tool errors that are distinguishable from empty results, and the
replacement of `run_no_throw` behaviour. Wire the read-only SQL validator from
v0.1 into the agent's execution path.

## v0.5: Microsoft Foundry hosted agents

Move the agent into Foundry as a hosted agent. Compare hosted execution against
self-hosted execution across identity, scaling, deployment, and operational
surface. Flip `enableHostedAgent`.

## v0.6: Foundry IQ and retrieval

Add knowledge and retrieval. Compare grounding a Text-to-SQL agent with schema
context against retrieval over documentation. Flip `enableAzureAiSearch` and
`enableFoundryIq`.

## v0.7: Memory

Short-term conversation state and long-term memory as distinct concerns. What
belongs in a checkpoint, what belongs in a memory store, and what belongs in
neither. Flip `enableLongTermMemory`.

## v0.8: Observability and evaluation

Instrument the agent against the Application Insights resource provisioned in
v0.1, using the canonical environment variable name that the legacy code got
wrong. Build the evaluation runner against the dataset defined in
[../evals/datasets/legacy-baseline.jsonl](../evals/datasets/legacy-baseline.jsonl).
Distinguish tracing, evaluation, and monitoring.

## v0.9: Performance and reliability

Latency budgets, streaming, concurrency, retries, and transient fault handling
across the network boundary that v0.1 introduced. Serverless resume behaviour as
a concrete reliability case.

## v0.10: Token economics

Token accounting, caching, context trimming strategies, model tiering, and the
cost consequences of each architectural choice made so far.

## v0.11: Security and governance

Content safety, prompt injection through database content, data boundaries, and
audit. Flip `enablePrivateNetworking` and compare the result against the
`AllowAllWindowsAzureIps` posture of v0.1.

## v0.12: Operational control

Environment promotion, configuration management, deployment safety, rollback,
and teardown discipline.

## v0.13: Distribution

Packaging and consumption. Flip `enableContainerRegistry` and revisit the
container path that the legacy repository ran manually, now expressed as code.

## v0.14: External hosting comparison

Compare Foundry hosted agents against external hosting on the same agent, the
same data, and the same evaluation set. Flip `enableExternalHosting`.

## Principles that hold across releases

The agent stays simple enough to read in one sitting.

Every claim about the legacy implementation cites a path in the reference
repository.

Every Azure schema assumption is verified against the live provider before it is
written into a template.

Optional capabilities are parameterised and default to off, so each release flips
a flag rather than restructuring what already exists.

Nothing is provisioned without the topology, region, billable resources,
compilation result, and exact command being shown first.

## Related

- [releases/v0.1.md](releases/v0.1.md)
- [releases/v0.2.md](releases/v0.2.md)
- [architecture/v0.1-azure-foundation.md](architecture/v0.1-azure-foundation.md)
- [architecture/v0.2-python-foundation.md](architecture/v0.2-python-foundation.md)
