"""Local-lite settings — bare-metal dev server with SQLite.

Run without Docker:
    python manage.py runserver --settings=config.settings_local

Or set the env var once per shell session:
    set DJANGO_SETTINGS_MODULE=config.settings_local   (Windows)
    export DJANGO_SETTINGS_MODULE=config.settings_local (Unix)
    python manage.py runserver
"""
import os

# Force console telemetry before base settings initialise OTel.
os.environ.setdefault('OTEL_EXPORTER', 'console')

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
