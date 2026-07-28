// -----------------------------------------------------------------------------
// Role assignments for the v0.1 foundation.
//
// Two principals are granted access: the developer running the deployment, and
// the user-assigned managed identity that a later release will attach to the
// agent. Both receive the narrowest role that satisfies the v0.1 workflow.
//
// Azure SQL access is deliberately absent from this file. Database permissions
// are granted inside the database with CREATE USER FROM EXTERNAL PROVIDER and a
// read-only role, not through Azure role-based access control, because a control
// plane role would not restrict data-plane statements.
//
// Role definition ids verified 2026-07-27 with: az role definition list --name <role>
// -----------------------------------------------------------------------------

targetScope = 'resourceGroup'

@description('Name of the Foundry resource that receives the role assignments.')
param foundryAccountName string

@description('Name of the key vault that receives the role assignments.')
param keyVaultName string

@description('Name of the Application Insights component that receives the role assignments.')
param applicationInsightsName string

@description('Principal id of the user-assigned managed identity.')
param managedIdentityPrincipalId string

@description('Object id of the deploying principal.')
param deployerPrincipalId string

@description('Principal type of the deploying principal.')
param deployerPrincipalType string

// Azure AI Developer: create and run project assets on a Foundry resource.
var azureAiDeveloperRoleId = '64702f94-c441-49e6-a78b-ef80e0188fee'

// Cognitive Services OpenAI User: call inference endpoints. Sufficient for the
// v0.1 smoke test; no authoring or key-listing permission is included.
var cognitiveServicesOpenAiUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

// Key Vault Secrets User: read secret values. No write, no management.
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

// Monitoring Metrics Publisher: emit telemetry without reading it back.
var monitoringMetricsPublisherRoleId = '3913510d-42f4-4e42-8a64-420c390055eb'

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' existing = {
  name: keyVaultName
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: applicationInsightsName
}

// --- Developer ---------------------------------------------------------------

resource deployerFoundryDeveloper 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundryAccount
  name: guid(foundryAccount.id, deployerPrincipalId, azureAiDeveloperRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', azureAiDeveloperRoleId)
    principalId: deployerPrincipalId
    principalType: deployerPrincipalType
  }
}

resource deployerFoundryInference 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundryAccount
  name: guid(foundryAccount.id, deployerPrincipalId, cognitiveServicesOpenAiUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAiUserRoleId)
    principalId: deployerPrincipalId
    principalType: deployerPrincipalType
  }
}

// --- Managed identity --------------------------------------------------------

resource identityFoundryInference 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundryAccount
  name: guid(foundryAccount.id, managedIdentityPrincipalId, cognitiveServicesOpenAiUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAiUserRoleId)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource identityKeyVaultSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVault.id, managedIdentityPrincipalId, keyVaultSecretsUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource identityMetricsPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: applicationInsights
  name: guid(applicationInsights.id, managedIdentityPrincipalId, monitoringMetricsPublisherRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringMetricsPublisherRoleId)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output assignedRoleCount int = 5
