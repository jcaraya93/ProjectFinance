# Local-Lite Deployment — Bare-Metal + SQLite

Run the application directly on your machine without Docker. Uses Django's built-in development server and a file-based SQLite database for the fastest possible iteration cycle.

## Prerequisites

- Python 3.12+
- A `.env` file in the project root (see [Environment Variables](#environment-variables))

## Quick Start

```bash
# Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Run migrations and start the dev server
python manage.py migrate --settings=config.settings_local
python manage.py runserver --settings=config.settings_local
```

Or set the env var once per shell session to skip the `--settings` flag:

```bash
set DJANGO_SETTINGS_MODULE=config.settings_local        # Windows
# export DJANGO_SETTINGS_MODULE=config.settings_local   # macOS/Linux
python manage.py migrate
python manage.py runserver
```

The app will be available at `http://localhost:8000/`.

## What Changes vs Docker Mode

| Aspect | Local-lite | Local Docker |
|--------|-----------|--------------|
| **Database** | SQLite (`db.sqlite3` file) | PostgreSQL 17 |
| **Web server** | Django dev server (`runserver`) | Gunicorn (3 workers) |
| **Static files** | Django's built-in serving | WhiteNoise |
| **HTTPS / security** | Disabled | Disabled (dev) / Enabled (prod) |
| **Telemetry** | Console exporter (default) | Configurable (console / OTLP) |
| **Settings module** | `config.settings_local` | `config.settings` |

## Settings Module

`config/settings_local.py` inherits from the base `config/settings.py` and overrides:

- **Database** → file-based SQLite at `db.sqlite3` (persistent across restarts)
- **Middleware** → strips WhiteNoise (not needed with `runserver`)
- **Static storage** → uses Django's default `StaticFilesStorage`
- **Security** → disables `SECURE_SSL_REDIRECT` and HSTS
- **Telemetry** → defaults `OTEL_EXPORTER` to `console`

## Environment Variables

The `.env` file is still loaded. Only `DJANGO_SECRET_KEY` is required; database variables are ignored in this mode.

| Variable | Required | Example | Description |
|----------|----------|---------|-------------|
| `DJANGO_SECRET_KEY` | Yes | `your-secret-key` | Django secret key |
| `DJANGO_DEBUG` | No | `True` | Enable debug mode (defaults to `False`) |
| `OTEL_EXPORTER` | No | `console` | Telemetry exporter (defaults to `console` in local-lite) |
| `OTEL_SERVICE_NAME` | No | `project-finance-local` | Service name in traces/metrics |

PostgreSQL variables (`POSTGRES_DB`, `POSTGRES_USER`, etc.) are present in `.env` but unused — the local-lite settings override the database to SQLite.

## Common Tasks

### Create a superuser

```bash
python manage.py createsuperuser --settings=config.settings_local
```

### Seed categories and classification rules

```bash
python manage.py seed_categories --settings=config.settings_local
```

### Run tests

Tests use their own settings module (`config.settings_test`) with an in-memory SQLite database:

```bash
pip install -r requirements-dev.txt
pytest core/tests/ -v
```

### Run a management command

```bash
python manage.py <command> --settings=config.settings_local
```

### Reset the database

```bash
# Windows
del db.sqlite3
python manage.py migrate --settings=config.settings_local

# macOS/Linux
rm db.sqlite3
python manage.py migrate --settings=config.settings_local
```

## Architecture

```
┌──────────┐       ┌──────────────────────────┐
│ Browser  │──:8000──▶│  Django dev server        │
└──────────┘       │  Python 3.12              │
                   │  Built-in static serving   │
                   └──────────┬───────────────┘
                              │
                   ┌──────────▼───────────────┐
                   │  db.sqlite3               │
                   │  SQLite (file-based)       │
                   └──────────────────────────┘
```

## When to Use This Mode

- **Fast iteration** — no container rebuild on code changes; just save and refresh.
- **Offline development** — no Docker daemon or network services needed.
- **Quick prototyping** — spin up a working app in seconds.

For production-like testing with PostgreSQL, use the [Local Docker](../deploy-local/README.md) mode instead.

## Limitations

- SQLite does not support some PostgreSQL-specific features (e.g., `JSONField` lookups, advanced aggregations). Most application features work identically, but edge cases may differ.
- No Gunicorn — the Django dev server is single-threaded and not suitable for load testing.
- No WhiteNoise — static file serving is handled by Django's development server, which does not compress or cache-bust.

## Troubleshooting

### `DJANGO_SECRET_KEY` not set
Ensure you have a `.env` file in the project root with at least `DJANGO_SECRET_KEY=your-secret-key`.

### Migrations fail with `no such table`
Run migrations first: `python manage.py migrate --settings=config.settings_local`

### Port 8000 already in use
Use a different port: `python manage.py runserver 8001 --settings=config.settings_local`
