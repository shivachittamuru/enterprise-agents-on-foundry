// -----------------------------------------------------------------------------
// Key Vault.
//
// v0.1 stores no secrets: Azure SQL is Entra-only and Foundry has local auth
// disabled. The vault exists so that later releases have a governed place to put
// values such as third-party API keys, and so the access model is established
// with role-based access control from the start rather than retrofitted.
// -----------------------------------------------------------------------------

targetScope = 'resourceGroup'

@description('Azure region.')
param location string

@description('Tags applied to every resource.')
param tags object

@minLength(3)
@maxLength(24)
@description('Name of the key vault.')
param keyVaultName string

@description('Whether the vault accepts public network traffic.')
param publicNetworkAccess string

resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: tenant().tenantId
    // Role-based access control rather than legacy access policies.
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    // Left off so that a learning environment can be torn down and rebuilt.
    enablePurgeProtection: null
    publicNetworkAccess: publicNetworkAccess
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

output keyVaultName string = keyVault.name
output keyVaultId string = keyVault.id
output keyVaultUri string = keyVault.properties.vaultUri
