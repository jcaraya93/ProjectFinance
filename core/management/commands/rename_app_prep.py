"""
Update django_migrations table: rename app 'transactions' -> 'core'.
Run this BEFORE `python manage.py migrate`.
"""

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Update django_migrations table: rename app "transactions" to "core"'

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE django_migrations SET app = 'core' WHERE app = 'transactions'"
            )
            count = cursor.rowcount
        self.stdout.write(self.style.SUCCESS(
            f'Updated {count} row(s) in django_migrations.'
        ))
