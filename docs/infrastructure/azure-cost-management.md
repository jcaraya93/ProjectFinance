# Complex Solution: Azure Cost Management

## Stop billing (keep infrastructure and data)

```bash
# Stop the Django container (scales to zero — no compute charges)
az containerapp update -g projectfinance-rg -n projectfinance-web --min-replicas 0 --max-replicas 0

# Stop PostgreSQL server (no compute charges, storage still billed ~$4/mo)
az postgres flexible-server stop -g projectfinance-rg -n projectfinance-db
```

## Resume

```bash
# Start PostgreSQL server
az postgres flexible-server start -g projectfinance-rg -n projectfinance-db

# Re-enable the Django container
az containerapp update -g projectfinance-rg -n projectfinance-web --min-replicas 0 --max-replicas 1
```

Note: Start PostgreSQL first — the container's entrypoint waits for the database.

## What still costs money when stopped

| Resource             | Stopped cost | Why                              |
|----------------------|-------------|----------------------------------|
| PostgreSQL storage   | ~$4/month   | 32 GB SSD is always allocated    |
| Container Registry   | ~$5/month   | Stores your Docker images        |
| VNet, DNS zone       | Free        | No charge for networking config  |
| Container App (0 replicas) | Free  | No compute when scaled to zero   |

**Total when stopped: ~$9/month** (storage + registry only)

## Delete everything (irreversible — destroys all data)

```bash
az group delete -n projectfinance-rg --yes
```
