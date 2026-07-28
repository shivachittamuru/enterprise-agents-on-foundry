// -----------------------------------------------------------------------------
// User-assigned managed identity.
//
// Created in v0.1 so that the eventual agent has a stable identity to be granted
// read-only database access, rather than inheriting a developer's credentials.
// -----------------------------------------------------------------------------

targetScope = 'resourceGroup'

@description('Azure region.')
param location string

@description('Tags applied to every resource.')
param tags object

@description('Name of the user-assigned managed identity.')
param managedIdentityName string

resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: managedIdentityName
  location: location
  tags: tags
}

output managedIdentityName string = managedIdentity.name
output managedIdentityId string = managedIdentity.id
output principalId string = managedIdentity.properties.principalId
output clientId string = managedIdentity.properties.clientId
