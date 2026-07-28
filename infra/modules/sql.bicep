// -----------------------------------------------------------------------------
// Azure SQL logical server and one serverless database.
//
// Microsoft Entra authentication only. There is no administratorLogin and no
// administratorLoginPassword anywhere in this template, so no SQL credential
// exists to be committed, rotated, or leaked.
//
// Schema verified 2026-07-27 against Microsoft.Sql/servers and
// Microsoft.Sql/servers/databases at API version 2023-08-01. The sampleName
// enum on databases confirms that AdventureWorksLT is provisioned by the
// control plane, so no data-plane seeding is required on the happy path.
// -----------------------------------------------------------------------------

targetScope = 'resourceGroup'

@description('Azure region.')
param location string

@description('Tags applied to every resource.')
param tags object

@description('Name of the Azure SQL logical server.')
param sqlServerName string

@description('Name of the database.')
param sqlDatabaseName string

@description('Sign-in name of the Microsoft Entra administrator.')
param entraAdminLogin string

@description('Object id of the Microsoft Entra administrator.')
param entraAdminObjectId string

@allowed([
  'User'
  'Group'
  'Application'
])
@description('Principal type of the Microsoft Entra administrator.')
param entraAdminPrincipalType string

@description('Provision the built-in AdventureWorksLT sample data.')
param useBuiltInSample bool

@description('Minutes of inactivity before auto-pause. Use -1 to disable.')
param autoPauseDelayMinutes int

@description('Maximum vCores for the serverless database.')
param maxVCores int

@description('Client IP address permitted to reach the server. Empty adds no rule.')
param allowedClientIpAddress string

@description('Whether the server accepts public network traffic.')
param publicNetworkAccess string

resource sqlServer 'Microsoft.Sql/servers@2023-08-01' = {
  name: sqlServerName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    version: '12.0'
    minimalTlsVersion: '1.2'
    publicNetworkAccess: publicNetworkAccess
    restrictOutboundNetworkAccess: 'Disabled'
    administrators: {
      administratorType: 'ActiveDirectory'
      // Entra-only. SQL authentication is refused outright.
      azureADOnlyAuthentication: true
      login: entraAdminLogin
      sid: entraAdminObjectId
      principalType: entraAdminPrincipalType
      tenantId: tenant().tenantId
    }
  }
}

// Serverless General Purpose. Auto-pause suits an intermittently used learning
// environment far better than a provisioned Basic or S0 database.
resource sqlDatabase 'Microsoft.Sql/servers/databases@2023-08-01' = {
  parent: sqlServer
  name: sqlDatabaseName
  location: location
  tags: tags
  sku: {
    name: 'GP_S_Gen5'
    tier: 'GeneralPurpose'
    family: 'Gen5'
    capacity: maxVCores
  }
  properties: {
    collation: 'SQL_Latin1_General_CP1_CI_AS'
    // 32 GiB is the smallest practical allocation for the sample.
    maxSizeBytes: 34359738368
    autoPauseDelay: autoPauseDelayMinutes
    minCapacity: json('0.5')
    zoneRedundant: false
    readScale: 'Disabled'
    requestedBackupStorageRedundancy: 'Local'
    sampleName: useBuiltInSample ? 'AdventureWorksLT' : null
  }
}

// Lets Azure-hosted callers reach the server. This rule grants no data access on
// its own, because Entra authentication and database role membership are still
// required.
resource allowAzureServices 'Microsoft.Sql/servers/firewallRules@2023-08-01' = if (publicNetworkAccess == 'Enabled') {
  parent: sqlServer
  name: 'AllowAllWindowsAzureIps'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// A single client address, not a range. Broad ranges are deliberately not
// supported by this module.
resource allowClientIp 'Microsoft.Sql/servers/firewallRules@2023-08-01' = if (publicNetworkAccess == 'Enabled' && !empty(allowedClientIpAddress)) {
  parent: sqlServer
  name: 'AllowDeveloperClient'
  properties: {
    startIpAddress: allowedClientIpAddress
    endIpAddress: allowedClientIpAddress
  }
}

output serverName string = sqlServer.name
output serverId string = sqlServer.id
output serverFqdn string = sqlServer.properties.fullyQualifiedDomainName
output databaseName string = sqlDatabase.name
output databaseId string = sqlDatabase.id
