#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

    # Load the correct .env file based on which settings module is active.
    # .env.local → Local dev (settings_local), .env → Docker/production.
    from pathlib import Path
    from dotenv import load_dotenv
    base_dir = Path(__file__).resolve().parent
    settings_mod = os.environ.get('DJANGO_SETTINGS_MODULE', '')
    if 'settings_local' in settings_mod and (base_dir / '.env.local').exists():
        load_dotenv(base_dir / '.env.local', override=True)
    else:
        load_dotenv(base_dir / '.env')

    # Bootstrap OpenTelemetry early so management commands (including
    # runserver's autoreloader) benefit from instrumentation.
    from config.observability import init_observability
    init_observability()

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
