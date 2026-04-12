# Azure Complex Deployment (Container Apps)

## Prerequisites

- Azure CLI installed and logged in (`az login`)
- GitHub repo with `AZURE_CREDENTIALS` secret configured

## Initial Setup (one-time)

### 1. Create resource group

```
az group create -n projectfinance-rg -l brazilsouth
```

### 2. Create parameters file

Copy `infra/main.parameters.json.example` and fill in real values:

```json
{
  "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
  "contentVersion": "1.0.0.0",
  "parameters": {
    "djangoSecretKey": { "value": "<random-64-char-string>" },
    "dbPassword": { "value": "<strong-password-with-upper-lower-numbers>" },
    "otelEndpoint": { "value": "https://otlp-gateway-prod-us-east-0.grafana.net/otlp" },
    "otelHeaders": { "value": "Authorization=Basic <your-grafana-cloud-token>" }
  }
}
```

### 3. Deploy infrastructure

```
az deployment group create -g projectfinance-rg --template-file infra/main.bicep --parameters infra/main.parameters.json
```

This creates: VNet, Container Registry, PostgreSQL, Container Apps (~15 min).

### 4. Create GitHub Actions service principal

```
az ad sp create-for-rbac --name "github-deploy" --role contributor --scopes /subscriptions/<subscription-id>/resourceGroups/projectfinance-rg --json-auth
```

Add the JSON output to GitHub → Settings → Secrets → Actions → `AZURE_CREDENTIALS`.

### 5. Deploy the app

Push code and trigger the workflow manually from GitHub → Actions → Deploy to Azure → Run workflow.

## Updating

### Code changes only

Trigger the GitHub Actions workflow — it builds and deploys automatically.

### Infrastructure changes (env vars, scaling, secrets)

```
az deployment group create -g projectfinance-rg --template-file infra/main.bicep --parameters infra/main.parameters.json
```

## Stop / Resume / Delete

See [azure-cost-management.md](azure-cost-management.md).

## Monthly cost

| Resource | Running | Stopped |
|---|---|---|
| PostgreSQL B1ms | ~$13 | ~$4 (storage) |
| Container Apps | ~$5-10 | $0 (scaled to zero) |
| Container Registry | ~$5 | ~$5 |
| **Total** | **~$23-28** | **~$9** |
