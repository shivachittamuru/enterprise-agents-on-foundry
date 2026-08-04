---
title: "ADR 004: Model Deployment Selection"
description: Record of the requested model being unavailable in the target region, the catalog evidence gathered, the substitute chosen, and the fail-loudly mechanism that prevents silent substitution.
author: shiva
ms.date: 2026-07-27
ms.topic: concept
keywords:
  - adr
  - model deployment
  - quota
estimated_reading_time: 5
---

## Status

Accepted, 2026-07-27. Requires review by the repository owner, because it
departs from an explicit instruction.

## Context

The v0.1 work order specified one inexpensive model deployment using
`gpt-5.1-mini` on the Global Standard SKU with 10K tokens per minute of quota in
`westus3`, and added a constraint:

> If `gpt-5.1-mini` Global Standard is unavailable because of subscription quota
> or catalog availability, fail with a clear message and expose the model name
> and version as parameters. Do not silently substitute a different model.

## Evidence

Two commands were run against subscription on 2026-07-27.

```console
az cognitiveservices model list --location westus3
az cognitiveservices usage list --location westus3
```

`gpt-5.1-mini` appears in neither result. It is absent from the regional catalog
and absent from the quota list, so it is not a quota shortfall that could be
resolved with a request.

The `gpt-5.1` family published in `westus3` is `gpt-5.1` (version 2025-11-13),
`gpt-5.1-chat`, `gpt-5.1-codex`, `gpt-5.1-codex-mini`, and `gpt-5.1-codex-max`
(version 2025-12-04). The family has no general-purpose mini member in this
region. The `codex-mini` variants are code-specialised and are not an equivalent
substitute for a Text-to-SQL and general reasoning workload.

Quota observed for `gpt-5.1` was 500 of 1000 already consumed. The candidate
alternatives had their full 1000 available.

Quota units are thousands of tokens per minute, so the requested 10K TPM maps to
`sku.capacity: 10`.

General-purpose mini models available in `westus3` with a Global Standard SKU:

| Model | Version | Global Standard quota | Notes |
| --- | --- | --- | --- |
| gpt-5.4-mini | 2026-03-17 | 1000 unused | Newest general-purpose mini in region |
| gpt-5-mini | 2025-08-07 | 1000 unused | Prior generation |
| gpt-4.1-mini | 2025-04-14 | Available | Older generation |
| o4-mini | 2025-04-16 | Available | Reasoning-specialised |

## Decision

Set the Bicep defaults to `gpt-5.4-mini` version `2026-03-17` on `GlobalStandard`
with capacity 10, and expose `modelName`, `modelVersion`, `modelSkuName`, and
`modelCapacity` as parameters surfaced through `.env.example` as
`AZURE_MODEL_NAME`, `AZURE_MODEL_VERSION`, `AZURE_MODEL_SKU_NAME`, and
`AZURE_MODEL_CAPACITY`.

`gpt-5.4-mini` was chosen over `gpt-5-mini` because it is the current
general-purpose mini in the region and its full quota is unused. Both remain
selectable without editing any template.

## Preventing silent substitution

A default value alone would not satisfy the instruction, because a reader could
end up on a different model without noticing. The enforcement is in code.

[model_catalog.py](../../src/enterprise_agents_on_foundry/setup/model_catalog.py)
queries the regional catalog and the quota list, then reports the first blocking
problem in a specific form: model absent, version absent, SKU not offered, no
quota entry, or insufficient remaining quota. When the model is absent it lists
the comparable models that do exist in the region and states plainly that no
substitution is made automatically.

[preprovision_check.py](../../scripts/preprovision_check.py) runs that check as
part of the azd pre-provision hook and exits non-zero on failure, so provisioning
stops before any resource is created rather than failing partway through a
deployment.

No fallback logic exists anywhere in the codebase. If the configured model cannot
be deployed, provisioning stops and the reader chooses.

## Consequences

The repository defaults to a model the work order did not name. That deviation is
recorded here rather than buried in a template, and the file is flagged for owner
review.

Model availability changes over time. When `gpt-5.1-mini` reaches `westus3`,
switching to it means setting two environment variables and reprovisioning, with
no code change.

The pre-provision check adds two Azure CLI calls, taking a few seconds, before
every provision. That cost is small against a failed deployment.

The check reads the catalog for the current subscription in the configured
region, so a reader in a different region or subscription sees results for their
own context rather than for the one recorded here.

## Related

- [003-use-azd-and-modular-bicep.md](003-use-azd-and-modular-bicep.md)
- [../architecture/v0.1-azure-foundation.md](../architecture/v0.1-azure-foundation.md)
