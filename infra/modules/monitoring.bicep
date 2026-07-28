// -----------------------------------------------------------------------------
// Log Analytics workspace and workspace-based Application Insights.
//
// Provisioned in v0.1 even though no agent emits telemetry yet, because the
// legacy project's tracing was wired to a manually created resource whose
// connection string drifted from the code. Creating the monitoring foundation
// with the rest of the platform removes that drift.
// -----------------------------------------------------------------------------

targetScope = 'resourceGroup'

@description('Azure region.')
param location string

@description('Tags applied to every resource.')
param tags object

@description('Name of the Log Analytics workspace.')
param logAnalyticsWorkspaceName string

@description('Name of the Application Insights component.')
param applicationInsightsName string

@minValue(7)
@maxValue(730)
@description('Retention in days. 30 is the lowest cost option that still allows week-over-week comparison.')
param retentionInDays int = 30

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsWorkspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: applicationInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalyticsWorkspace.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
    // Entra-authenticated ingestion only. Instrumentation keys are not accepted.
    DisableLocalAuth: true
  }
}

output logAnalyticsWorkspaceName string = logAnalyticsWorkspace.name
output logAnalyticsWorkspaceId string = logAnalyticsWorkspace.id
output applicationInsightsName string = applicationInsights.name
output applicationInsightsId string = applicationInsights.id

// The connection string is intentionally not an output. Retrieve it at run time
// with: az monitor app-insights component show --query connectionString
