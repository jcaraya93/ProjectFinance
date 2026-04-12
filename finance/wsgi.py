"""
WSGI config for finance project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""

import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'finance.settings')

from finance.observability import init_observability  # noqa: E402
init_observability()

from django.core.wsgi import get_wsgi_application  # noqa: E402
application = get_wsgi_application()
