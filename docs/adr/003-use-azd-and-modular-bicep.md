---
title: "ADR 003: Use the Azure Developer CLI with Modular Bicep"
description: Decision to define the Azure foundation as subscription-scoped modular Bicep driven by the azd lifecycle, replacing the manual Docker, ACR, and AKS deployment path.
author: shiva
ms.date: 2026-07-27
ms.topic: concept
keywords:
  - adr
  - bicep
  - azd
estimated_reading_time: 6
---

## Status

Accepted, 2026-07-27.

## Context

The legacy `deployment-guide.md` documents a hand-run sequence: build a Docker
image, sign in to a container registry, push, then configure an Azure Web App or
an AKS cluster through the portal, passing secrets as `-e` flags. Nothing about
that sequence is captured in code, so environments drift and teardown is manual.

The curriculum needs infrastructure that a reader can create, inspect, and
remove repeatedly without accumulating cost or unexplained state.

## Decision

Define the foundation as Bicep under `infra/`, driven by the Azure Developer CLI.

Scope `infra/main.bicep` to the subscription, so the template creates its own
resource group. Placing the resource group inside the template makes the entire
footprint, including its own container, reviewable in one place and removable in
one operation.

Put all composition in modules. `main.bicep` declares parameters, computes
naming, creates the resource group, and passes everything to a single `platform`
module, which in turn calls one module per concern: `monitoring`, `identity`,
`keyvault`, `foundry`, `sql`, `rbac`, and an optional `registry`.

Drive the lifecycle with `azure.yaml`. A pre-provision hook runs
[preprovision_check.py](../../scripts/preprovision_check.py), which validates the
workstation, the Azure sign-in, and model availability, then prints the topology
and the billable resources before anything is created. A post-provision hook runs
[write_env_from_azd.py](../../scripts/write_env_from_azd.py), which copies
outputs into `.env`, writing only keys already declared in `.env.example`.

Declare no services section. v0.1 provisions infrastructure only. Adding a
services block before there is an application to host would create an empty
deployment target.

## Naming and idempotency

Resource names derive from a fixed prefix, the environment name, and a
deterministic suffix computed as
`substring(uniqueString(subscription().subscriptionId, environmentName, projectName), 0, 6)`.
The same inputs always produce the same names, so reprovisioning updates the
existing resources rather than creating parallel ones. `AZURE_RESOURCE_NAME_SUFFIX`
overrides the computed value when a specific name is needed.

## Outputs are a contract

Outputs from `main.bicep` are named to match the keys in `.env.example`
exactly, because azd copies them into the environment. A test asserts that every
uppercase output name has a corresponding key, so the two files cannot drift.

Secret-valued outputs are excluded. The Application Insights connection string
is deliberately not an output, since deployment outputs are readable by anyone
with access to deployment history. A test enforces this across `main.bicep`,
`platform.bicep`, and `monitoring.bicep`.

## Feature flags

Seven capabilities are parameterised and default to `false`:
`enableContainerRegistry`, `enableAzureAiSearch`, `enableFoundryIq`,
`enableLongTermMemory`, `enablePrivateNetworking`, `enableHostedAgent`, and
`enableExternalHosting`.

Each corresponds to a later release. Declaring them now fixes the shape of the
template so future releases flip a flag and add a module rather than restructure
what already exists. A test asserts that each still defaults to `false` in v0.1,
so a capability cannot be switched on by accident.

## Consequences

The complete footprint is reviewable as code and removable with `azd down`.

`az bicep build --file infra/main.bicep` compiles the whole graph with no
warnings, which catches property and API-version errors before any deployment.

Security decisions that a compiler cannot check are asserted as text in
[test_infrastructure.py](../../tests/test_infrastructure.py): Entra-only SQL
authentication, the absence of any password parameter, `disableLocalAuth` on the
Foundry resource, RBAC rather than access policies on Key Vault, no admin user
on the registry, and no SQL data-plane role in the RBAC module.

Provisioning requires the deployer principal object identifier, because the SQL
server needs a Microsoft Entra administrator and there is no password fallback.
The pre-provision check resolves it with `az ad signed-in-user show`.

## Alternatives considered

Terraform was rejected for this repository. Bicep needs no state backend, which
removes a setup step from every lesson, and the Azure resource schemas used here
were verified directly against the live provider.

Resource-group-scoped Bicep with the group created out of band was rejected
because it splits the footprint across two places and makes teardown a two-step
manual operation.

Raw `az deployment sub create` without azd was rejected because the hook model is
what makes the pre-provision safety gate and the post-provision environment file
automatic rather than remembered.

## Related

- [002-use-azure-sql-adventureworks-lt.md](002-use-azure-sql-adventureworks-lt.md)
- [../architecture/v0.1-azure-foundation.md](../architecture/v0.1-azure-foundation.md)
