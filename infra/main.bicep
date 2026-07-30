// -----------------------------------------------------------------------------
// enterprise-agents-on-foundry :: v0.1 Azure foundation
//
// Subscription-scoped so that a single deployment can create the resource group
// and everything inside it. All resource composition lives in modules; this file
// only decides names, tags, and which optional capabilities are switched on.
//
// Verified against live provider schemas on 2026-07-27. See
// docs/architecture/v0.1-azure-foundation.md for the evidence table.
// -----------------------------------------------------------------------------

targetScope = 'subscription'

// -----------------------------------------------------------------------------
// Naming and tagging
// -----------------------------------------------------------------------------

@minLength(2)
@maxLength(24)
@description('Environment name. Used in resource names and in the environment tag.')
param environmentName string = 'dev'

@description('Azure region for every resource in this deployment.')
param location string = 'westus3'

@description('Project name. Used as a resource name prefix and as the project tag.')
param projectName string = 'enterprise-agents-on-foundry'

@description('Value for the owner tag.')
param ownerTag string = 'shiva'

@description('Name of the resource group to create or update.')
param resourceGroupName string = 'rg-${projectName}-${environmentName}'

@description('Optional explicit suffix for globally unique names. Leave empty to derive one deterministically.')
param resourceNameSuffix string = ''

// -----------------------------------------------------------------------------
// Identity of the deploying principal
// -----------------------------------------------------------------------------

@description('Object id of the principal that will administer Azure SQL and use Foundry. Obtain with: az ad signed-in-user show --query id -o tsv')
param deployerPrincipalId string

@description('Sign-in name of the Microsoft Entra administrator for Azure SQL.')
param deployerPrincipalName string

@allowed([
  'User'
  'Group'
  'Application'
])
@description('Principal type of the Microsoft Entra administrator for Azure SQL.')
param deployerPrincipalType string = 'User'

// -----------------------------------------------------------------------------
// Model deployment
//
// The model name and version are parameters on purpose. Catalog availability
// varies by subscription and region, so scripts/validate_model_availability.py
// checks them before provisioning rather than letting the deployment fail late.
// -----------------------------------------------------------------------------

@description('Name of the model deployment created inside the Foundry resource.')
param modelDeploymentName string = 'chat-model'

@description('Model to deploy. Verify availability with: az cognitiveservices model list --location <region>')
param modelName string = 'gpt-5.4-mini'

@description('Model version. Verify with the same command used for modelName.')
param modelVersion string = '2026-03-17'

@description('Model publisher format.')
param modelFormat string = 'OpenAI'

@allowed([
  'GlobalStandard'
  'DataZoneStandard'
  'Standard'
])
@description('Deployment SKU. GlobalStandard is the pay-as-you-go option used for v0.1.')
param modelSkuName string = 'GlobalStandard'

@minValue(1)
@maxValue(100)
@description('Capacity in thousands of tokens per minute. 10 equals 10K TPM.')
param modelCapacity int = 10

// -----------------------------------------------------------------------------
// Azure SQL
// -----------------------------------------------------------------------------

@description('Name of the Azure SQL database.')
param sqlDatabaseName string = 'AdventureWorksLT'

@description('Provision the built-in AdventureWorksLT sample. When false, seed with the fallback script under database/seeds/.')
param sqlUseBuiltInSample bool = true

@description('Minutes of inactivity before the serverless database auto-pauses. Use -1 to disable auto-pause.')
param sqlAutoPauseDelayMinutes int = 60

@description('Maximum vCores for the serverless database.')
param sqlMaxVCores int = 1

@description('Expose the Azure SQL server on a public endpoint restricted by firewall rules. v0.1 needs this to reach the database from a local notebook. Firewall rules cannot be created when this is false.')
param sqlPublicNetworkAccessEnabled bool = true

@description('Create the AllowAllWindowsAzureIps rule so Azure-hosted callers can reach the SQL server.')
param allowAzureServices bool = true

@description('Single client IP address permitted to reach the SQL server. Obtain with: curl -s https://api.ipify.org. Leave empty to add no client rule.')
param developerClientIp string = ''

@description('Apply the SecurityControl=Ignore tag that exempts the SQL server from the tenant AzureSQL_PublicNetwork_Modify policy. Without it that policy rewrites publicNetworkAccess to Disabled and every firewall rule fails. Off by default because it weakens a governance control.')
param applyPublicNetworkPolicyExemptionTag bool = true

// -----------------------------------------------------------------------------
// Feature flags
//
// Every flag defaults to false. v0.1 provisions only what it uses; nothing is
// created because it might be useful in a later release.
// -----------------------------------------------------------------------------

@description('Provision an Azure Container Registry. Deferred to the hosted-agent release.')
param enableContainerRegistry bool = false

@description('Provision Azure AI Search. Not used in v0.1.')
param enableAzureAiSearch bool = false

@description('Provision Foundry IQ dependencies. Not used in v0.1.')
param enableFoundryIq bool = false

@description('Provision long-term memory storage. Not used in v0.1.')
param enableLongTermMemory bool = false

@description('Provision private endpoints and disable public network access. Not used in v0.1.')
param enablePrivateNetworking bool = false

@description('Provision hosted-agent-specific dependencies. Not used in v0.1.')
param enableHostedAgent bool = false

@description('Provision external hosting compute. Not used in v0.1.')
param enableExternalHosting bool = false

// -----------------------------------------------------------------------------
// Derived values
// -----------------------------------------------------------------------------

var suffix = empty(resourceNameSuffix)
  ? substring(uniqueString(subscription().subscriptionId, environmentName, projectName), 0, 6)
  : resourceNameSuffix

var tags = {
  project: projectName
  environment: environmentName
  managedBy: 'bicep'
  owner: ownerTag
}

// -----------------------------------------------------------------------------
// Resource group
// -----------------------------------------------------------------------------

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-11-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

// -----------------------------------------------------------------------------
// Platform composition
// -----------------------------------------------------------------------------

module platform 'modules/platform.bicep' = {
  name: 'platform-${environmentName}'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    environmentName: environmentName
    suffix: suffix
    deployerPrincipalId: deployerPrincipalId
    deployerPrincipalName: deployerPrincipalName
    deployerPrincipalType: deployerPrincipalType
    modelDeploymentName: modelDeploymentName
    modelName: modelName
    modelVersion: modelVersion
    modelFormat: modelFormat
    modelSkuName: modelSkuName
    modelCapacity: modelCapacity
    sqlDatabaseName: sqlDatabaseName
    sqlUseBuiltInSample: sqlUseBuiltInSample
    sqlAutoPauseDelayMinutes: sqlAutoPauseDelayMinutes
    sqlMaxVCores: sqlMaxVCores
    sqlPublicNetworkAccessEnabled: sqlPublicNetworkAccessEnabled
    allowAzureServices: allowAzureServices
    developerClientIp: developerClientIp
    applyPublicNetworkPolicyExemptionTag: applyPublicNetworkPolicyExemptionTag
    enableContainerRegistry: enableContainerRegistry
    enablePrivateNetworking: enablePrivateNetworking
  }
}

// -----------------------------------------------------------------------------
// Outputs
//
// Names mirror the keys in .env.example so that azd env get-values maps across
// with no translation step.
// -----------------------------------------------------------------------------

output AZURE_SUBSCRIPTION_ID string = subscription().subscriptionId
output AZURE_TENANT_ID string = subscription().tenantId
output AZURE_LOCATION string = location
output AZURE_RESOURCE_GROUP string = resourceGroup.name
output AZURE_RESOURCE_NAME_SUFFIX string = suffix

output AZURE_FOUNDRY_RESOURCE_NAME string = platform.outputs.foundryAccountName
output AZURE_FOUNDRY_PROJECT_NAME string = platform.outputs.foundryProjectName
output AZURE_FOUNDRY_PROJECT_ENDPOINT string = platform.outputs.foundryProjectEndpoint
output AZURE_FOUNDRY_ACCOUNT_ENDPOINT string = platform.outputs.foundryAccountEndpoint

output AZURE_MODEL_DEPLOYMENT_NAME string = modelDeploymentName
output AZURE_MODEL_NAME string = modelName
output AZURE_MODEL_VERSION string = modelVersion

output AZURE_SQL_SERVER_NAME string = platform.outputs.sqlServerName
output AZURE_SQL_SERVER_FQDN string = platform.outputs.sqlServerFqdn
output AZURE_SQL_DATABASE_NAME string = sqlDatabaseName
output AZURE_SQL_AUTHENTICATION string = 'entra'
output AZURE_SQL_PUBLIC_NETWORK_ACCESS string = platform.outputs.sqlPublicNetworkAccess

output AZURE_LOG_ANALYTICS_WORKSPACE_NAME string = platform.outputs.logAnalyticsWorkspaceName
output AZURE_KEY_VAULT_NAME string = platform.outputs.keyVaultName
output AZURE_MANAGED_IDENTITY_NAME string = platform.outputs.managedIdentityName
output AZURE_MANAGED_IDENTITY_CLIENT_ID string = platform.outputs.managedIdentityClientId

output ENABLE_CONTAINER_REGISTRY bool = enableContainerRegistry
output ENABLE_AZURE_AI_SEARCH bool = enableAzureAiSearch
output ENABLE_FOUNDRY_IQ bool = enableFoundryIq
output ENABLE_LONG_TERM_MEMORY bool = enableLongTermMemory
output ENABLE_PRIVATE_NETWORKING bool = enablePrivateNetworking
output ENABLE_HOSTED_AGENT bool = enableHostedAgent
output ENABLE_EXTERNAL_HOSTING bool = enableExternalHosting
