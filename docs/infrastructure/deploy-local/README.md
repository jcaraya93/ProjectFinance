# Local Deployment — Docker Compose

Run the full application locally using Docker Compose. This starts Django + Gunicorn behind PostgreSQL 17. Telemetry is exported directly to Grafana Cloud.

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine + Compose plugin)
- A `.env` file in the project root (see below)

## Quick Start

```bash
# Start the stack (builds the Django image on first run)
docker compose up -d --build

# Verify both services are running
docker compose ps

# Open the app
# http://localhost:8000
```

The entrypoint script automatically:
1. Waits for PostgreSQL to be healthy (up to 60s)
2. Runs `python manage.py migrate --noinput`
3. Collects static files via WhiteNoise
4. Starts Gunicorn with 3 workers

## Services

| Service | Image | Port | Description |
|---------|-------|------|-------------|
| **web** | `projectfinance-web` (built from Dockerfile) | 8000 | Django + Gunicorn, WhiteNoise for static files |
| **db** | `postgres:17` | 5432 | PostgreSQL with health check, data persisted in `pgdata` volume |

## Environment Variables

The `.env` file is loaded by the web container. Required variables:

| Variable | Example | Description |
|----------|---------|-------------|
| `DJANGO_SECRET_KEY` | `your-secret-key` | Django secret key |
| `DJANGO_DEBUG` | `True` | Enable debug mode |
| `POSTGRES_DB` | `projectfinance` | Database name |
| `POSTGRES_USER` | `projectfinance` | Database user |
| `POSTGRES_PASSWORD` | `projectfinance` | Database password |
| `POSTGRES_HOST` | `localhost` | Overridden to `db` by docker-compose |
| `POSTGRES_PORT` | `5432` | Database port |

### Telemetry (optional)

| Variable | Example | Description |
|----------|---------|-------------|
| `OTEL_EXPORTER` | `otlp-http` | Exporter type: `console`, `otlp` (gRPC), or `otlp-http` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `https://otlp-gateway-....grafana.net/otlp` | OTLP endpoint (Grafana Cloud) |
| `OTEL_EXPORTER_OTLP_HEADERS` | `Authorization=Basic ...` | Auth header for the OTLP endpoint |
| `OTEL_SERVICE_NAME` | `project-finance-local` | Service name in traces/metrics |

Set `OTEL_EXPORTER=console` to print telemetry to stdout instead of sending it to a remote endpoint.

## Common Tasks

### Create a superuser

```bash
docker compose exec web python manage.py createsuperuser
```

### Seed categories and classification rules

```bash
docker compose exec web python manage.py seed_categories
```

### View logs

```bash
# All services
docker compose logs -f

# Web only
docker compose logs -f web
```

### Run a Django management command

```bash
docker compose exec web python manage.py <command>
```

### Reset the database

```bash
docker compose down -v     # removes the pgdata volume
docker compose up -d       # recreates from scratch
```

### Rebuild after code changes

```bash
docker compose up -d --build
```

## Architecture

```
┌──────────┐       ┌──────────────────────────┐
│ Browser  │──:8000──▶│  web                     │
└──────────┘       │  Django + Gunicorn        │──── OTLP/HTTP ───▶ Grafana Cloud
                   │  Python 3.12-slim         │    (traces, metrics, logs)
                   │  WhiteNoise (static)      │
                   └──────────┬───────────────┘
                              │ :5432
                   ┌──────────▼───────────────┐
                   │  db                       │
                   │  PostgreSQL 17            │
                   │  Volume: pgdata           │
                   └──────────────────────────┘
```

## Troubleshooting

### `database is not ready` on startup
The entrypoint retries for 60 seconds. If PostgreSQL is slow to start, increase the retry count in `docker/entrypoint.sh`.

### Port 8000 or 5432 already in use
Stop the conflicting process or change the port mapping in `docker-compose.yml`:
```yaml
ports:
  - "8001:8000"  # use localhost:8001 instead
```

### Stale containers after switching branches
```bash
docker compose down --volumes --remove-orphans
docker compose up -d --build
```

## See Also

- [Local-Lite (bare-metal + SQLite)](../deploy-local-lite/README.md) — fastest iteration, no Docker needed
- [Azure Simple Deployment (Single VM)](../deploy-azure-simple/azure-deploy-simple.md)
- [Azure Complex Deployment (Container Apps)](../deploy-azure-complex/azure-deploy-complex.md)
