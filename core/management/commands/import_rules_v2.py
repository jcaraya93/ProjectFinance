from django.core.management.base import BaseCommand, CommandError

from core.models import User
from core.services.rules_v2 import import_v1_rules


class Command(BaseCommand):
    help = 'Copy a user\'s V1 classification rules into Rules V2 (idempotent; V1 is untouched).'

    def add_arguments(self, parser):
        parser.add_argument('email', help='Email of the user whose rules to import')

    def handle(self, *args, **options):
        try:
            user = User.objects.get(email=options['email'])
        except User.DoesNotExist:
            raise CommandError(f'No user with email {options["email"]}')
        created, existing, missing = import_v1_rules(user)
        self.stdout.write(self.style.SUCCESS(
            f'Imported {created} rules ({existing} already existed, {missing} skipped: no matching V2 category or no conditions).'
        ))
