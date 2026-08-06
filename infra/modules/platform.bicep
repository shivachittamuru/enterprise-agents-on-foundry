// -----------------------------------------------------------------------------
// Platform composition for the v0.1 foundation.
//
// Runs at resource group scope and wires the individual modules together. Kept
// separate from main.bicep so that the subscription-scoped concern (creating the
// resource group) stays independent of what goes inside it.
// -----------------------------------------------------------------------------

targetScope = 'resourceGroup'

@description('Azure region for every resource.')
param location string

@description('Tags applied to every resource.')
param tags object

@description('Environment name, used in resource names.')
param environmentName string

@minLength(4)
@maxLength(12)
@description('Suffix that makes globally unique names deterministic per environment.')
param suffix string

@description('Object id of the principal that administers Azure SQL and uses Foundry.')
param deployerPrincipalId string

@description('Sign-in name of the Microsoft Entra administrator for Azure SQL.')
param deployerPrincipalName string

@description('Principal type of the Microsoft Entra administrator for Azure SQL.')
param deployerPrincipalType string

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

@description('Name of the Azure SQL database.')
param sqlDatabaseName string

@description('Provision the built-in AdventureWorksLT sample.')
param sqlUseBuiltInSample bool

@description('Minutes of inactivity before the serverless database auto-pauses.')
param sqlAutoPauseDelayMinutes int

@description('Maximum vCores for the serverless database.')
param sqlMaxVCores int

@description('Whether the Azure SQL server exposes a public endpoint restricted by firewall rules.')
param sqlPublicNetworkAccessEnabled bool

@description('Create the AllowAllWindowsAzureIps rule on the Azure SQL server.')
param allowAzureServices bool

@description('Single client IP address permitted to reach the SQL server.')
param developerClientIp string

@description('Apply the SecurityControl=Ignore tag exempting the SQL server from the public network governance policy.')
param applyPublicNetworkPolicyExemptionTag bool

@description('Provision an Azure Container Registry.')
param enableContainerRegistry bool

@description('Disable public network access on data-plane resources.')
param enablePrivateNetworking bool

// -----------------------------------------------------------------------------
// Names
//
// Azure SQL server names and Key Vault names are globally unique, so they carry
// the suffix. Names scoped to the resource group do not need it.
// -----------------------------------------------------------------------------

var namePrefix = 'eaof-${environmentName}'
var publicNetworkAccess = enablePrivateNetworking ? 'Disabled' : 'Enabled'
var containerRegistryName = 'cr${replace(namePrefix, '-', '')}${suffix}'

// Private networking wins over the SQL switch, so enabling private endpoints can
// never leave a public endpoint behind.
var sqlPublicEndpoint = sqlPublicNetworkAccessEnabled && !enablePrivateNetworking

// -----------------------------------------------------------------------------
// Modules
// -----------------------------------------------------------------------------

module monitoring 'monitoring.bicep' = {
  name: 'monitoring'
  params: {
    location: location
    tags: tags
    logAnalyticsWorkspaceName: 'log-${namePrefix}'
    applicationInsightsName: 'appi-${namePrefix}'
  }
}

module managedIdentity 'identity.bicep' = {
  name: 'identity'
  params: {
    location: location
    tags: tags
    managedIdentityName: 'id-${namePrefix}'
  }
}

module keyVault 'keyvault.bicep' = {
  name: 'keyvault'
  params: {
    location: location
    tags: tags
    keyVaultName: 'kv-${namePrefix}-${suffix}'
    publicNetworkAccess: publicNetworkAccess
  }
}

module foundry 'foundry.bicep' = {
  name: 'foundry'
  params: {
    location: location
    tags: tags
    foundryAccountName: 'aif-${namePrefix}-${suffix}'
    foundryProjectName: 'proj-${namePrefix}'
    modelDeploymentName: modelDeploymentName
    modelName: modelName
    modelVersion: modelVersion
    modelFormat: modelFormat
    modelSkuName: modelSkuName
    modelCapacity: modelCapacity
    publicNetworkAccess: publicNetworkAccess
  }
}

module sql 'sql.bicep' = {
  name: 'sql'
  params: {
    location: location
    tags: tags
    sqlServerName: 'sql-${namePrefix}-${suffix}'
    sqlDatabaseName: sqlDatabaseName
    entraAdminLogin: deployerPrincipalName
    entraAdminObjectId: deployerPrincipalId
    entraAdminPrincipalType: deployerPrincipalType
    useBuiltInSample: sqlUseBuiltInSample
    autoPauseDelayMinutes: sqlAutoPauseDelayMinutes
    maxVCores: sqlMaxVCores
    sqlPublicNetworkAccessEnabled: sqlPublicEndpoint
    allowAzureServices: allowAzureServices
    developerClientIp: developerClientIp
    applyPublicNetworkPolicyExemptionTag: applyPublicNetworkPolicyExemptionTag
  }
}

module registry 'registry.bicep' = if (enableContainerRegistry) {
  name: 'registry'
  params: {
    location: location
    tags: tags
    containerRegistryName: containerRegistryName
  }
}

module rbac 'rbac.bicep' = {
  name: 'rbac'
  params: {
    foundryAccountName: foundry.outputs.accountName
    keyVaultName: keyVault.outputs.keyVaultName
    applicationInsightsName: monitoring.outputs.applicationInsightsName
    managedIdentityPrincipalId: managedIdentity.outputs.principalId
    foundryProjectPrincipalId: foundry.outputs.projectPrincipalId
    deployerPrincipalId: deployerPrincipalId
    deployerPrincipalType: deployerPrincipalType
    enableContainerRegistry: enableContainerRegistry
    containerRegistryName: containerRegistryName
  }
}

// -----------------------------------------------------------------------------
// Outputs
// -----------------------------------------------------------------------------

output foundryAccountName string = foundry.outputs.accountName
output foundryAccountEndpoint string = foundry.outputs.accountEndpoint
output foundryProjectName string = foundry.outputs.projectName
output foundryProjectId string = foundry.outputs.projectId
output foundryProjectEndpoint string = foundry.outputs.projectEndpoint

output sqlServerName string = sql.outputs.serverName
output sqlServerFqdn string = sql.outputs.serverFqdn
output sqlPublicNetworkAccess string = sql.outputs.publicNetworkAccess
output sqlDatabaseName string = sql.outputs.databaseName

output logAnalyticsWorkspaceName string = monitoring.outputs.logAnalyticsWorkspaceName
output applicationInsightsName string = monitoring.outputs.applicationInsightsName
output keyVaultName string = keyVault.outputs.keyVaultName
output managedIdentityName string = managedIdentity.outputs.managedIdentityName
output managedIdentityClientId string = managedIdentity.outputs.clientId
output containerRegistryName string = enableContainerRegistry ? registry!.outputs.containerRegistryName : ''
output containerRegistryLoginServer string = enableContainerRegistry ? registry!.outputs.loginServer : ''
