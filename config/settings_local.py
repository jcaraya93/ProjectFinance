"""Local settings — dev server with SQLite.

Run without Docker:
    python manage.py runserver --settings=config.settings_local

Or set the env var once per shell session:
    set DJANGO_SETTINGS_MODULE=config.settings_local   (Windows)
    export DJANGO_SETTINGS_MODULE=config.settings_local (Unix)
    python manage.py runserver
"""
import os
from pathlib import Path

# Load .env.local (Local defaults) instead of .env (Docker defaults).
# This must happen before base settings are imported so that the correct
# OTEL_EXPORTER and DJANGO_SECRET_KEY values are already in the environment.
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / '.env.local', override=True)

from .settings import *  # noqa: F401, F403

# ── Database: file-based SQLite (persistent across restarts) ──
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# ── Static files: use Django's built-in dev serving ───────────
MIDDLEWARE = [m for m in MIDDLEWARE if 'whitenoise' not in m]
STORAGES = {
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
}

# ── Disable production security ───────────────────────────────
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
