---
title: Evaluation Datasets
description: Structure and intent of the Text-to-SQL evaluation datasets for AdventureWorksLT, from the v0.1 prose baseline to the v0.4 typed contract.
author: shiva
ms.date: 2026-07-30
ms.topic: reference
keywords:
  - evaluation
  - dataset
estimated_reading_time: 6
---

## Two datasets

`text-to-sql.jsonl` is the executable dataset. Every field is typed by
`enterprise_agents_on_foundry.evaluation.cases`, and every assertion is decidable
without a model and without a human.

`legacy-baseline.jsonl` is the v0.1 record, kept unchanged. It stated
expectations in prose, so nothing could grade it. All twenty-five cases were
migrated into the typed contract under their original identifiers, and four
cases were added for the repair and empty-result paths that v0.1 did not cover.

The runner arrives in a later v0.4 slice. Fixing the contract first keeps the
runner from being shaped around whatever the agent happens to do well.

## Why the legacy dataset was not reused

`../langgraph-agents-on-azure/backend/evaluations/data/evaluation_input.json`
contains five question and ground-truth pairs over Chinook, with prose answers
such as "Iron Maiden, with total sales of $138.6". The shape is sound and is
carried forward. The content is not, because the dataset changed to
AdventureWorksLT and because five cases cover only the success path.

## Record structure

| Field | Meaning |
| --- | --- |
| `id` | Stable identifier, unique within the dataset |
| `category` | Behaviour class being measured |
| `difficulty` | `easy`, `medium`, or `hard`, for reporting only |
| `question` | Natural language input, phrased as a person would phrase it |
| `expected` | The assertions the case makes |
| `tags` | Labels used to select a subset, including the v0.1 categories |
| `notes` | Why the case exists and the failure mode it targets |

Unknown fields are rejected, so a typo fails the load rather than passing
unnoticed.

## Expected assertions

Every assertion is optional, and absent means "not asserted" rather than
"asserted to be zero". A case states only what it cares about.

| Field | Asserts |
| --- | --- |
| `disposition` | What the model decides it can do with the question |
| `outcome` | The terminal outcome the caller receives |
| `query_status` | What happens at the database boundary |
| `required_sql_patterns` | Expressions that must match the generated SQL, case-insensitively |
| `forbidden_sql_patterns` | Expressions that must not match, vacuously true when no SQL exists |
| `required_tables` | Qualified tables the statement must reference |
| `expected_columns` | Columns the result set must contain |
| `min_rows`, `max_rows` | Row-count bounds |
| `required_answer_text`, `forbidden_answer_text` | Substrings the answer must or must not contain |
| `min_repairs`, `max_repairs` | Repair-attempt bounds |
| `database_calls` | Exact number of query-tool calls |
| `max_model_calls` | Model-call budget |
| `max_latency_ms` | Optional end-to-end latency budget |

Ground truth stays structural rather than a literal query string, because many
correct queries produce the same answer. Grading a literal string would reward
imitation instead of correctness.

Combinations that could never pass are rejected while loading: an inverted row
range, a clarification case that also demands tables, a succeeded outcome paired
with a rejected query status, a pattern that is both required and forbidden, or
an expectation that asserts nothing at all.

## Coverage

Twenty-nine cases across seven categories.

| Category | Cases | Measures |
| --- | --- | --- |
| `query_correctness` | 14 | The statement reaches the right tables, filters, and shape |
| `answer_correctness` | 2 | The prose answer says what the rows support |
| `safety` | 4 | Refusing writes, and treating database content as data |
| `clarification` | 3 | Asking rather than guessing |
| `unsupported` | 2 | Naming the boundary of the schema |
| `empty_result` | 2 | Reporting no rows as no rows |
| `repair` | 2 | Recovering within one bounded retry |

## Legacy coverage

The v0.1 categories survive as tags on the migrated cases.

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

Ten of the twenty-nine cases expect an outcome other than an answer. An agent
that answers everything is not correct, it is compliant, and the difference is
what these categories measure.

`ambiguous-001` narrowed on migration. v0.1 accepted either a clarifying
question or a stated assumption; the v0.3 graph makes asking the only
deterministic option, so the migrated case asserts a clarification.

## Relationship to the v0.1 controls

The three `unsafe_write` cases are already covered by two mechanisms provisioned
in that release: the read-only database principal and
`assert_read_only_sql`. The evaluation set adds a third check at the behavioural
layer, so a refusal can be verified as a deliberate response rather than as an
error surfaced from a rejected statement. Each case asserts zero database calls
and forbids destructive keywords in any statement that is produced, so reaching
the validator counts as a model failure rather than as a pass.

`unsafe-003` maps directly to the single-statement rule, and `broad-002` maps to
`DATABASE_MAX_RESULT_ROWS`.

## Running the dataset

`eaof-evaluate` grades the agent against these cases and writes
`results.jsonl` and `summary.json` under `artifacts/evaluations/`, which is
ignored: per-case evidence quotes retrieved values and model prose.

```bash
uv run eaof-evaluate --mode deterministic
uv run eaof-evaluate --mode live
uv run eaof-evaluate --case unsafe-001
uv run eaof-evaluate --tag safety
```

Deterministic mode replays [../fixtures/deterministic-agent.jsonl](../fixtures/deterministic-agent.jsonl)
through the real graph, so the evaluator can be exercised with no cloud, no
model, and no driver. It carries one script per case and the run refuses to
start if a case has none. Live mode uses the configured Foundry deployment and
Azure SQL over Microsoft Entra authentication.

The four `safety` cases also carry the `safety` tag, so the whole safety subset
can be selected by either name.

## Related

- [../../docs/current-state/modernization-risks.md](../../docs/current-state/modernization-risks.md)
- [../../docs/roadmap.md](../../docs/roadmap.md)
