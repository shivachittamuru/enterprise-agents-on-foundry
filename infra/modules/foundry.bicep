// -----------------------------------------------------------------------------
// Microsoft Foundry resource, project, and one model deployment.
//
// This is the current Foundry shape: a Microsoft.CognitiveServices account with
// kind 'AIServices' and allowProjectManagement enabled, plus a child project.
// It replaces the classic hub-and-project pair built on
// Microsoft.MachineLearningServices.
//
// Schema verified 2026-07-27 against Microsoft.CognitiveServices/accounts,
// /accounts/projects, and /accounts/deployments at API version 2025-06-01.
// -----------------------------------------------------------------------------

targetScope = 'resourceGroup'

@description('Azure region.')
param location string

@description('Tags applied to every resource.')
param tags object

@minLength(2)
@maxLength(64)
@description('Name of the Foundry resource.')
param foundryAccountName string

@minLength(2)
@maxLength(64)
@description('Name of the Foundry project.')
param foundryProjectName string

@description('Name of the model deployment.')
param modelDeploymentName string

@description('Model to deploy.')
param modelName string

@description('Model version.')
param modelVersion string

@description('Model publisher format.')
param modelFormat string

@description('Deployment SKU.')
param modelSkuName string

@description('Capacity in thousands of tokens per minute.')
param modelCapacity int

@description('Whether the account accepts public network traffic.')
param publicNetworkAccess string

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: foundryAccountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    // Turns the account into a Foundry resource that can host projects.
    allowProjectManagement: true
    // Required for token-based data-plane access.
    customSubDomainName: foundryAccountName
    // Microsoft Entra only. API keys are never issued, so none can leak.
    disableLocalAuth: true
    publicNetworkAccess: publicNetworkAccess
  }
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundryAccount
  name: foundryProjectName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: foundryProjectName
    description: 'Learning project for enterprise-agents-on-foundry v0.1.'
  }
}

// A single deployment. v0.1 uses it for provisioning proof and one smoke test.
resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundryAccount
  name: modelDeploymentName
  sku: {
    name: modelSkuName
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: modelFormat
      name: modelName
      version: modelVersion
    }
    versionUpgradeOption: 'OnceCurrentVersionExpired'
  }
}

output accountName string = foundryAccount.name
output accountId string = foundryAccount.id
output accountEndpoint string = foundryAccount.properties.endpoint
output accountPrincipalId string = foundryAccount.identity.principalId

output projectName string = foundryProject.name
output projectId string = foundryProject.id
output projectPrincipalId string = foundryProject.identity.principalId

// The authoritative endpoint map returned by the provider. Keys vary by
// capability, so the validation script reads from this rather than assuming one.
output projectEndpoints object = foundryProject.properties.endpoints

// Convenience form used to populate .env. Matches the documented Foundry project
// endpoint layout; projectEndpoints above remains the source of truth.
output projectEndpoint string = 'https://${foundryAccount.name}.services.ai.azure.com/api/projects/${foundryProject.name}'

output modelDeploymentName string = modelDeployment.name
