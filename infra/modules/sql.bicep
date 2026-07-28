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

@description('Whether the server exposes a public endpoint. v0.1 uses a public endpoint restricted by firewall rules. Set false only when private endpoints are in place, because no firewall rule can be created while this is false.')
param sqlPublicNetworkAccessEnabled bool = true

@description('Create the AllowAllWindowsAzureIps rule so Azure-hosted callers can reach the server. Requires sqlPublicNetworkAccessEnabled.')
param allowAzureServices bool = true

@description('Single client IP address permitted to reach the server, for local notebook use. Empty adds no rule. Ranges are deliberately not supported.')
param developerClientIp string = ''

@description('Apply the SecurityControl=Ignore tag that exempts this server from the AzureSQL_PublicNetwork_Modify governance policy. Off by default. Turning it on weakens a tenant security control, so it is an explicit decision.')
param applyPublicNetworkPolicyExemptionTag bool = false

// The Azure SQL resource provider rejects any firewall rule write while the
// public endpoint is off, with error DenyPublicEndpointEnabled. Both rules below
// are therefore gated on sqlPublicNetworkAccessEnabled, never on the IP alone.
var publicNetworkAccess = sqlPublicNetworkAccessEnabled ? 'Enabled' : 'Disabled'

// Tag name and value are fixed by the policy definition, not chosen here.
var serverTags = applyPublicNetworkPolicyExemptionTag ? union(tags, { SecurityControl: 'Ignore' }) : tags

resource sqlServer 'Microsoft.Sql/servers@2023-08-01' = {
  name: sqlServerName
  location: location
  tags: serverTags
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

// The 0.0.0.0 to 0.0.0.0 pair is the documented sentinel for "Azure services",
// not an address range. It grants no data access on its own, because Entra
// authentication and database role membership are still required.
resource azureServicesRule 'Microsoft.Sql/servers/firewallRules@2023-08-01' =
  if (sqlPublicNetworkAccessEnabled && allowAzureServices) {
    parent: sqlServer
    name: 'AllowAllWindowsAzureIps'
    properties: {
      startIpAddress: '0.0.0.0'
      endIpAddress: '0.0.0.0'
    }
  }

// A single client address, not a range. An unrestricted 0.0.0.0 to
// 255.255.255.255 rule is deliberately not expressible through this module.
resource developerClientRule 'Microsoft.Sql/servers/firewallRules@2023-08-01' =
  if (sqlPublicNetworkAccessEnabled && !empty(developerClientIp)) {
    parent: sqlServer
    name: 'AllowDeveloperClient'
    properties: {
      startIpAddress: developerClientIp
      endIpAddress: developerClientIp
    }
  }

output serverName string = sqlServer.name
output publicNetworkAccess string = sqlServer.properties.publicNetworkAccess
output serverId string = sqlServer.id
output serverFqdn string = sqlServer.properties.fullyQualifiedDomainName
output databaseName string = sqlDatabase.name
output databaseId string = sqlDatabase.id
