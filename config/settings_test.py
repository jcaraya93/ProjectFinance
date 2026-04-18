"""Test settings — uses SQLite so tests run without PostgreSQL."""
from .settings import *  # noqa: F401, F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

# Strip middleware that requires extra packages not in requirements-dev.txt
MIDDLEWARE = [m for m in MIDDLEWARE if 'whitenoise' not in m]

# Use default static files storage instead of whitenoise
STORAGES = {
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
}

# Disable OpenTelemetry noise in tests
import os
os.environ.setdefault('OTEL_EXPORTER', 'console')
