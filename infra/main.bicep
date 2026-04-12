// Azure infrastructure for Project Finance
// Deploy: az deployment group create -g projectfinance-rg --template-file infra/main.bicep

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Django secret key')
@secure()
param djangoSecretKey string

@description('PostgreSQL admin password')
@secure()
param dbPassword string

// ── Variables ────────────────────────────────────────────────

var prefix = 'projectfinance'
var acrName = '${prefix}cr'
var dbServerName = '${prefix}-db'
var dbName = 'projectfinance'
var dbUser = 'projectfinance'
var containerEnvName = '${prefix}-env'
var containerAppName = '${prefix}-web'
var imageName = '${acrName}.azurecr.io/projectfinance:latest'
var vnetName = '${prefix}-vnet'
var appSubnetName = 'app-subnet'
var dbSubnetName = 'db-subnet'

// ── Virtual Network ──────────────────────────────────────────

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: { addressPrefixes: ['10.0.0.0/16'] }
    subnets: [
      {
        name: appSubnetName
        properties: {
          addressPrefix: '10.0.0.0/23'
          delegations: [
            {
              name: 'containerApps'
              properties: { serviceName: 'Microsoft.App/environments' }
            }
          ]
        }
      }
      {
        name: dbSubnetName
        properties: {
          addressPrefix: '10.0.2.0/24'
          delegations: [
            {
              name: 'postgresql'
              properties: { serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers' }
            }
          ]
        }
      }
    ]
  }
}

// ── Container Registry ───────────────────────────────────────

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: true }
}

// ── PostgreSQL ───────────────────────────────────────────────

resource dbServer 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: dbServerName
  location: location
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '17'
    administratorLogin: dbUser
    administratorLoginPassword: dbPassword
    storage: { storageSizeGB: 32 }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    network: {
      delegatedSubnetResourceId: vnet.properties.subnets[1].id
      privateDnsZoneArmResourceId: privateDnsZone.id
    }
  }
}

resource privateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: '${dbServerName}.private.postgres.database.azure.com'
  location: 'global'
}

resource privateDnsVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: privateDnsZone
  name: '${vnetName}-link'
  location: 'global'
  properties: {
    virtualNetwork: { id: vnet.id }
    registrationEnabled: false
  }
}

resource db 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: dbServer
  name: dbName
  properties: {}
}

// ── Container Apps Environment (VNet-integrated) ─────────────

resource containerEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: containerEnvName
  location: location
  properties: {
    vnetConfiguration: {
      infrastructureSubnetId: vnet.properties.subnets[0].id
      internal: false
    }
  }
}

// ── Container App ────────────────────────────────────────────

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
      }
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
        {
          name: 'django-secret-key'
          value: djangoSecretKey
        }
        {
          name: 'db-password'
          value: dbPassword
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'web'
          image: imageName
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'DJANGO_SECRET_KEY', secretRef: 'django-secret-key' }
            { name: 'DJANGO_DEBUG', value: 'False' }
            { name: 'DJANGO_ALLOWED_HOSTS', value: '*' }
            { name: 'POSTGRES_DB', value: dbName }
            { name: 'POSTGRES_USER', value: dbUser }
            { name: 'POSTGRES_PASSWORD', secretRef: 'db-password' }
            { name: 'POSTGRES_HOST', value: dbServer.properties.fullyQualifiedDomainName }
            { name: 'POSTGRES_PORT', value: '5432' }
            { name: 'OTEL_SERVICE_NAME', value: 'project-finance' }
            { name: 'OTEL_EXPORTER', value: 'console' }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
      }
    }
  }
}

// ── Outputs ──────────────────────────────────────────────────

output appUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output acrLoginServer string = acr.properties.loginServer
output dbHost string = dbServer.properties.fullyQualifiedDomainName
