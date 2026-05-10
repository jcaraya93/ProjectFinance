"""Management command to auto-match transfer transaction pairs."""
from django.core.management.base import BaseCommand

from core.models import User
from core.services.pair_matcher import auto_match_transfers


class Command(BaseCommand):
    help = 'Auto-match transfer transactions into pairs (Internal + Credit categories).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Show what would be matched without saving.')
        parser.add_argument('--user', type=str, help='Email of the user to match for. Defaults to all users.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        user_email = options.get('user')

        if user_email:
            users = User.objects.filter(email=user_email)
            if not users.exists():
                self.stderr.write(f'User not found: {user_email}')
                return
        else:
            users = User.objects.all()

        for user in users:
            self.stdout.write(f'Matching transfers for {user.email}...')
            result = auto_match_transfers(user, dry_run=dry_run)
            prefix = '[DRY RUN] ' if dry_run else ''
            self.stdout.write(self.style.SUCCESS(
                f'{prefix}Paired: {result.paired}, Unmatched: {result.unmatched}, Skipped: {result.skipped}'
            ))
