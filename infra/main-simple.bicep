// Azure infrastructure for Project Finance — Single VM deployment
// Deploy: az deployment group create -g projectfinance-vm-rg --template-file infra/main-simple.bicep

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('VM admin username')
param adminUsername string = 'azureuser'

@description('SSH public key for VM access')
param sshPublicKey string

@description('VM size')
param vmSize string = 'Standard_B1s'

@description('GitHub repo URL')
param repoUrl string = 'https://github.com/jcaraya93/ProjectFinance.git'

@description('Django secret key')
@secure()
param djangoSecretKey string

@description('PostgreSQL password')
@secure()
@minLength(8)
param dbPassword string

@description('Grafana Cloud OTLP endpoint')
param otelEndpoint string = ''

@description('Grafana Cloud OTLP auth header')
@secure()
param otelHeaders string = ''

@description('Domain name (e.g. project-finance.cc). Sets DJANGO_ALLOWED_HOSTS. Leave empty for *.')
param domain string = ''

@description('IP address allowed for SSH access (e.g. 203.0.113.1). Use * for any.')
param sshSourceIp string = '*'

// ── Variables ────────────────────────────────────────────────

var prefix = 'projectfinance'
var vmName = '${prefix}-vm'
var nsgName = '${prefix}-nsg'
var vnetName = '${prefix}-vnet'
var subnetName = 'default'
var publicIpName = '${prefix}-ip'
var nicName = '${prefix}-nic'

// ── Network Security Group ───────────────────────────────────

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-01-01' = {
  name: nsgName
  location: location
  properties: {
    securityRules: [
      {
        name: 'SSH'
        properties: {
          priority: 1000
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '22'
          sourceAddressPrefix: sshSourceIp
          destinationAddressPrefix: '*'
        }
      }
      {
        name: 'HTTP'
        properties: {
          priority: 1001
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '80'
          sourceAddressPrefix: '*'
          destinationAddressPrefix: '*'
        }
      }
      {
        name: 'HTTPS'
        properties: {
          priority: 1002
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '443'
          sourceAddressPrefix: '*'
          destinationAddressPrefix: '*'
        }
      }
    ]
  }
}

// ── Virtual Network ──────────────────────────────────────────

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: { addressPrefixes: ['10.0.0.0/16'] }
  }
}

resource subnet 'Microsoft.Network/virtualNetworks/subnets@2024-01-01' = {
  parent: vnet
  name: subnetName
  properties: {
    addressPrefix: '10.0.0.0/24'
    networkSecurityGroup: { id: nsg.id }
  }
}

// ── Public IP ────────────────────────────────────────────────

resource publicIp 'Microsoft.Network/publicIPAddresses@2024-01-01' = {
  name: publicIpName
  location: location
  sku: { name: 'Standard' }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

// ── Network Interface ────────────────────────────────────────

resource nic 'Microsoft.Network/networkInterfaces@2024-01-01' = {
  name: nicName
  location: location
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          subnet: { id: subnet.id }
          publicIPAddress: { id: publicIp.id }
          privateIPAllocationMethod: 'Dynamic'
        }
      }
    ]
  }
}

// ── Virtual Machine ──────────────────────────────────────────

resource vm 'Microsoft.Compute/virtualMachines@2024-03-01' = {
  name: vmName
  location: location
  properties: {
    hardwareProfile: { vmSize: vmSize }
    osProfile: {
      computerName: vmName
      adminUsername: adminUsername
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            {
              path: '/home/${adminUsername}/.ssh/authorized_keys'
              keyData: sshPublicKey
            }
          ]
        }
      }
    }
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer: '0001-com-ubuntu-server-jammy'
        sku: '22_04-lts-gen2'
        version: 'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        managedDisk: { storageAccountType: 'Standard_LRS' }
        diskSizeGB: 30
      }
    }
    networkProfile: {
      networkInterfaces: [{ id: nic.id }]
    }
  }
}

// ── Cloud-init: Install Docker + Deploy App ──────────────────

var allowedHosts = empty(domain) ? '*' : domain

var envFileContent = 'DJANGO_SECRET_KEY=${djangoSecretKey}\nDJANGO_DEBUG=False\nDJANGO_ALLOWED_HOSTS=${allowedHosts}\nPOSTGRES_DB=projectfinance\nPOSTGRES_USER=projectfinance\nPOSTGRES_PASSWORD=${dbPassword}\nOTEL_SERVICE_NAME=project-finance-azure-simple\nOTEL_EXPORTER=${empty(otelEndpoint) ? 'console' : 'otlp-http'}\nOTEL_EXPORTER_OTLP_ENDPOINT=${otelEndpoint}\nOTEL_EXPORTER_OTLP_HEADERS=${otelHeaders}\nSECURE_SSL_REDIRECT=${empty(domain) ? 'False' : 'True'}'

var setupScript = '#!/bin/bash\nset -e\nexec > /var/log/cloud-init-app.log 2>&1\n\necho "=== Installing Docker ==="\ncurl -fsSL https://get.docker.com | sh\nusermod -aG docker ${adminUsername}\napt-get install -y docker-compose-plugin git\n\necho "=== Cloning repository ==="\ngit clone ${repoUrl} /opt/projectfinance\nchown -R ${adminUsername}:${adminUsername} /opt/projectfinance\n\necho "=== Writing .env.prod ==="\nprintf \'%b\' \'${envFileContent}\' > /opt/projectfinance/.env.prod\nchmod 600 /opt/projectfinance/.env.prod\nchown ${adminUsername}:${adminUsername} /opt/projectfinance/.env.prod\n\necho "=== Starting application (web + db) ==="\ncd /opt/projectfinance\ndocker compose -f docker-compose.prod.yml up -d web db\n\necho "=== Setup complete ==="\necho "Next: update DNS to point to this IP, then run init-letsencrypt.sh"'

resource vmExtension 'Microsoft.Compute/virtualMachines/extensions@2024-03-01' = {
  parent: vm
  name: 'setup-app'
  location: location
  properties: {
    publisher: 'Microsoft.Azure.Extensions'
    type: 'CustomScript'
    typeHandlerVersion: '2.1'
    autoUpgradeMinorVersion: true
    protectedSettings: {
      script: base64(setupScript)
    }
  }
}

// ── Outputs ──────────────────────────────────────────────────

output vmPublicIp string = publicIp.properties.ipAddress
output sshCommand string = 'ssh ${adminUsername}@${publicIp.properties.ipAddress}'
output appUrl string = empty(domain) ? 'http://${publicIp.properties.ipAddress}:8000' : 'https://${domain}'
