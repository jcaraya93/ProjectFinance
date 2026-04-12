# Azure Simple Deployment (Single VM)

## Prerequisites

- Azure CLI installed and logged in (`az login`)
- An SSH key pair (`ssh-keygen -t ed25519`)

## Initial Setup (one-time)

### 1. Create resource group

```
az group create -n projectfinance-vm-rg -l brazilsouth
```

### 2. Deploy VM

```
az deployment group create -g projectfinance-vm-rg --template-file infra/main-simple.bicep --parameters sshPublicKey="$(cat ~/.ssh/id_ed25519.pub)"
```

Or on Windows (PowerShell):

```
az deployment group create -g projectfinance-vm-rg --template-file infra/main-simple.bicep --parameters sshPublicKey="$(Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub)"
```

This creates: VM (B1s) + VNet + NSG (ports 80, 443, 22) + public IP (~5 min).

Note the output: `vmPublicIp` and `sshCommand`.

### 3. SSH into the VM and configure

```
ssh azureuser@<vm-ip>
```

```bash
cd /opt/projectfinance

# Create .env.prod from the example
nano .env.prod
# Fill in: DJANGO_SECRET_KEY, POSTGRES_PASSWORD, DJANGO_ALLOWED_HOSTS,
#          OTEL_* settings, DOMAIN
```

### 4. First deployment

Clone your repo on the VM or use the GitHub Actions workflow:

```bash
# On the VM:
cd /opt/projectfinance
git clone https://github.com/jcaraya93/ProjectFinance.git .
cp /path/to/.env.prod .env.prod

# Get SSL certificate
chmod +x docker/init-letsencrypt.sh
./docker/init-letsencrypt.sh yourdomain.com your@email.com

# Start everything
docker compose -f docker-compose.prod.yml up -d
```

### 5. Configure GitHub Actions (for subsequent deploys)

Add these secrets to GitHub → Settings → Secrets → Actions:

| Secret | Value |
|---|---|
| `VM_HOST` | Your VM's public IP |
| `VM_SSH_KEY` | Contents of your private SSH key (`~/.ssh/id_ed25519`) |

Then trigger: Actions → Deploy to Azure VM (Simple) → Run workflow.

## Updating

Trigger the GitHub Actions workflow — it rsync's code and restarts containers.

## Stop / Resume / Delete

### Stop (keep data, ~$1/mo for disk only)
```
az vm deallocate -g projectfinance-vm-rg -n projectfinance-vm
```

### Resume
```
az vm start -g projectfinance-vm-rg -n projectfinance-vm
```

### Delete everything
```
az group delete -n projectfinance-vm-rg --yes
```

## Monthly cost

| Resource | Running | Stopped |
|---|---|---|
| VM B1s (1 vCPU, 1 GiB) | ~$4-8 | $0 |
| OS Disk (30 GB) | ~$1 | ~$1 |
| Public IP | ~$3 | ~$3 |
| **Total** | **~$8-12** | **~$4** |
