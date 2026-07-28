---
title: Evaluation Datasets
description: Structure and intent of the v0.1 planned evaluation dataset for Text-to-SQL over AdventureWorksLT.
author: shiva
ms.date: 2026-07-27
ms.topic: reference
keywords:
  - evaluation
  - dataset
estimated_reading_time: 4
---

## Status in v0.1

`legacy-baseline.jsonl` defines expected behaviour and metadata only. The
LangGraph evaluation runner arrives in a later release, so nothing executes these
cases yet.

Defining them now fixes the target before the agent is written, which keeps the
implementation from being shaped around whatever it happens to do well.

## Why the legacy dataset was not reused

`../langgraph-agents-on-azure/backend/evaluations/data/evaluation_input.json`
contains five question and ground-truth pairs over Chinook, with prose answers
such as "Iron Maiden, with total sales of $138.6". The shape is sound and is
carried forward. The content is not, because the dataset changed to
AdventureWorksLT and because five cases cover only the success path.

## Record structure

| Field | Meaning |
| --- | --- |
| `id` | Stable identifier, prefixed by category |
| `category` | Behaviour class being probed |
| `difficulty` | `easy`, `medium`, or `hard` |
| `question` | Natural language input, phrased as a person would phrase it |
| `expected_behavior` | What a correct agent does, which is not always to answer |
| `expected_tables` | Tables a correct answer touches, empty when the case should be refused |
| `expected_sql_shape` | Structural description rather than a literal query |
| `notes` | Why the case exists and the failure mode it targets |
| `status` | `planned` in v0.1 |

Ground truth is described structurally rather than as an exact query string,
because many correct queries produce the same answer. Grading a literal string
would reward imitation instead of correctness.

## Coverage

Twenty-five cases across twelve categories.

| Category | Cases | Probes |
| --- | --- | --- |
| `basic_selection` | 2 | Schema discovery and row limiting |
| `filtering` | 2 | Business term mapping and implied conditions |
| `aggregate` | 2 | Counts and averages with stated exclusions |
| `join` | 3 | Two-table through four-table join paths |
| `grouping` | 2 | `GROUP BY`, and `HAVING` rather than `WHERE` |
| `ordering` | 1 | Ordering with null and zero handling |
| `date` | 3 | Date bucketing, boundaries, and historical data |
| `ambiguous` | 2 | Clarifying rather than guessing |
| `nonexistent_field` | 1 | Refusing rather than substituting a plausible column |
| `nonexistent_entity` | 1 | Knowing the boundary of the schema |
| `unsafe_write` | 3 | Refusing destructive requests, including a stacked one |
| `excessively_broad` | 2 | Bounding results and disclosing the bound |
| `prompt_injection` | 1 | Treating database content as data, never as instructions |

Half the cases have an expected behaviour other than answering. An agent that
answers everything is not correct, it is compliant, and the difference is what
these categories measure.

## Relationship to the v0.1 controls

The three `unsafe_write` cases are already covered by two mechanisms provisioned
in this release: the read-only database principal and
`assert_read_only_sql`. The evaluation set adds a third check at the behavioural
layer, so a refusal can be verified as a deliberate response rather than as an
error surfaced from a rejected statement.

`unsafe-003` maps directly to the single-statement rule, and `broad-002` maps to
`DATABASE_MAX_RESULT_ROWS`.

## Related

- [../../docs/current-state/modernization-risks.md](../../docs/current-state/modernization-risks.md)
- [../../docs/roadmap.md](../../docs/roadmap.md)
