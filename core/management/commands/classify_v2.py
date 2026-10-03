from django.core.management.base import BaseCommand, CommandError

from core.models import User
from core.services.rules_v2 import classify_transactions_v2


class Command(BaseCommand):
    help = ('Assign Categories V2 to a user\'s transactions using Rules V2. '
            'Writes only the V2 fields; V1 categories and rules are untouched.')

    def add_arguments(self, parser):
        parser.add_argument('email', help='Email of the user whose transactions to classify')
        parser.add_argument('--dry-run', action='store_true', help='Report what would change without saving')

    def handle(self, *args, **options):
        try:
            user = User.objects.get(email=options['email'])
        except User.DoesNotExist:
            raise CommandError(f'No user with email {options["email"]}')
        total, changed, manual, unmatched = classify_transactions_v2(user, dry_run=options['dry_run'])
        verb = 'Would classify' if options['dry_run'] else 'Classified'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {changed} of {total} transactions ({manual} manual skipped, {unmatched} matched no rule).'
        ))
