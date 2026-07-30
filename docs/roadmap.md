---
title: Roadmap
description: Planned progression from the brownfield baseline through LangGraph safety, Microsoft Foundry, observability, durable state, knowledge, governance, and distribution.
author: shiva
ms.date: 2026-07-30
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

Versions v0.1 through v0.3 are shipped. Later versions are planned rather than
committed, and their scope may move as the material develops.

## v0.1: Brownfield baseline and new repository

Shipped. Analyse the legacy backend, establish the modern repository, and
provision the Azure foundation. See [releases/v0.1.md](releases/v0.1.md).

## v0.2: uv packaging and clean source layout

Shipped. Establish the typed, testable application foundation with `uv`, a
`src/` package layout, one configuration boundary, one database boundary, thin
adapters, and notebooks that import production code. See
[releases/v0.2.md](releases/v0.2.md).

## v0.3: LangGraph 1.2 Text-to-SQL graph

Shipped. Rebuild the Text-to-SQL agent on LangGraph 1.2 as an explicit
`StateGraph` with typed state, pure routing functions, deterministic read-only
validation before any execution, one bounded repair attempt, and structured
success or failure output. Prebuilt agent patterns were evaluated and rejected:
for Text-to-SQL an explicit graph is clearer and safer, because the model does
not choose when to act. Replaces the fabricated tool call identifier and the
six-message rolling window. See [releases/v0.3.md](releases/v0.3.md).

## v0.4: Deterministic safety and evaluation

Make answer quality and safety measurable before adding platform complexity.
Build the deterministic evaluation runner from the existing dataset, establish
query and answer correctness checks, expand SQL validator coverage, and record
unsafe-query rejection, repair success, latency, and model-call evidence.

## v0.5: Foundry models and identity

Treat model selection and Microsoft Entra identity as explicit architecture.
Pin one approved Foundry deployment first, then compare small and strong models,
routing policy, quality, latency, and cost without changing graph control flow.

## v0.6: Foundry hosted-agent deployment

Deploy the same bounded agent on the Foundry hosted runtime. Compare local and
hosted execution across identity, packaging, scaling, failure handling, and
operational surface. Flip `enableHostedAgent` only in this release.

## v0.7: OpenTelemetry and economics scorecard

Instrument graph, model, and database boundaries with OpenTelemetry. Produce one
scorecard covering latency distributions, task success, tokens, model and tool
calls, cost per request, and cost per successful request. Keep trace attributes
free of credentials, SQL-sensitive values, and private result data.

## v0.8: Durable PostgreSQL checkpointing

Add one supported PostgreSQL checkpointer for durable graph state and recovery.
Define thread identity, retention, resume behavior, and failure recovery before
introducing conversational memory. Do not restore the legacy duplicate SQLite
and PostgreSQL implementations.

## v0.9: Foundry Memory comparison

Separate durable execution state from remembered user knowledge. Compare
Foundry Memory with a custom long-term store using the same scenarios, retention
rules, quality checks, privacy constraints, and economics scorecard. Flip
`enableLongTermMemory` for this experiment.

## v0.10: Foundry IQ business glossary

Add a business glossary as knowledge alongside deterministic schema metadata.
Measure whether Foundry IQ improves business-term grounding, query correctness,
and evidence quality without weakening table and column constraints. Flip
`enableAzureAiSearch` and `enableFoundryIq` only here.

## v0.11: Governance and human approval

Add approval boundaries for consequential actions and make authorization, data
scope, prompt-injection handling, trace redaction, and audit evidence explicit.
Compare private networking and gateway controls with the teaching environment's
public endpoint posture.

## v0.12: Teams and Microsoft 365 distribution

Expose the approved agent through Teams and Microsoft 365 surfaces while keeping
identity, authorization, citations, and operational budgets consistent. Compare
notebook, API, Teams, Microsoft 365 Copilot, and MCP consumption boundaries.

## v0.13: External-hosting comparison

Compare Foundry hosted agents with external hosting on the same graph, model,
data, evaluation set, and scorecard. Measure scaling, cold start, reliability,
operational ownership, and cost. Flip `enableExternalHosting` only here.

## How Foundry capabilities map to the project

The project tracks eight architecture layers. The first implementation remains
small enough to teach; a later experiment introduces the platform alternative.

| Layer | Initial project implementation | Later experiment |
| --- | --- | --- |
| Models | One approved Foundry deployment | Small and strong model routing and model router |
| Tools | Read-only typed SQL tools | MCP, private catalog, or toolbox |
| Knowledge | Schema metadata and business glossary | Foundry IQ |
| Runtime | Local execution, then Foundry hosted | External Container Apps comparison |
| Memory | LangGraph PostgreSQL checkpointing | Foundry Memory or custom long-term store |
| Observability | OpenTelemetry traces and deterministic evaluations | Continuous Foundry evaluation and dashboard |
| Governance | SQL validator and operational budgets | Entra, RBAC, guardrails, gateway, and control plane |
| Distribution | Notebook and API | Teams, Microsoft 365 Copilot, and MCP |

## Permanent architecture dimensions

Every release records the dimensions it can affect. A release may report a
dimension as not measured with a reason; it must not invent a value or postpone
the dimension merely because a later release adds richer tooling.

### Performance

* Time to first token
* End-to-end latency
* SQL execution latency
* p50, p95, and p99 latency
* Throughput and concurrency
* Cold-start duration

### Reliability

* Task-success rate
* Valid-SQL rate
* Repair success rate
* Timeout recovery
* Checkpoint recovery
* Dependency-failure handling

### Quality

* Query correctness
* Answer correctness
* Grounded explanation
* Citation and evidence correctness
* Unsafe-query rejection

### Economics

* Input and output tokens
* Model calls per query
* Tool calls per query
* Cost per request
* Cost per successful request
* Marginal quality per additional dollar

### Operational control

* Step, token, and cost budgets
* Query timeout and row limit
* Concurrency limit
* Circuit breaker
* Human-approval boundary

### Security

* Authentication and authorization
* Data scope and read-only enforcement
* Prompt-injection resistance
* Trace redaction
* Auditability

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

Every release captures applicable performance, reliability, quality, economics,
operational-control, and security evidence. Missing evidence is recorded as not
measured with a reason, never replaced with an estimate.

## Related

* [releases/v0.1.md](releases/v0.1.md)
* [releases/v0.2.md](releases/v0.2.md)
* [releases/v0.3.md](releases/v0.3.md)
* [architecture/v0.1-azure-foundation.md](architecture/v0.1-azure-foundation.md)
* [architecture/v0.2-python-foundation.md](architecture/v0.2-python-foundation.md)
* [architecture/v0.3-modern-langgraph-agent.md](architecture/v0.3-modern-langgraph-agent.md)
