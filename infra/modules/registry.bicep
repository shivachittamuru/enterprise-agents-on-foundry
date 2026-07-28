// -----------------------------------------------------------------------------
// Azure Container Registry.
//
// Not provisioned in v0.1. An empty registry carries a recurring cost with no
// consumer, and v0.1 containerises nothing. The module is written and kept
// compiling so the hosted-agent release can switch enableContainerRegistry to
// true without new template work.
// -----------------------------------------------------------------------------

targetScope = 'resourceGroup'

@description('Azure region.')
param location string

@description('Tags applied to every resource.')
param tags object

@minLength(5)
@maxLength(50)
@description('Name of the container registry.')
param containerRegistryName string

@allowed([
  'Basic'
  'Standard'
  'Premium'
])
@description('Registry SKU.')
param skuName string = 'Basic'

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: containerRegistryName
  location: location
  tags: tags
  sku: {
    name: skuName
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    // Entra tokens only. Admin user credentials are never enabled.
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

output containerRegistryName string = containerRegistry.name
output containerRegistryId string = containerRegistry.id
output loginServer string = containerRegistry.properties.loginServer
