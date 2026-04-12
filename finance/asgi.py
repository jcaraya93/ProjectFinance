"""
ASGI config for finance project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""

import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'finance.settings')

from finance.observability import init_observability  # noqa: E402
init_observability()

from django.core.asgi import get_asgi_application  # noqa: E402
application = get_asgi_application()
