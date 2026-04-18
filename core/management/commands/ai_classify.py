from django.core.management.base import BaseCommand
from core.services.ai_classifier import apply_ai_classifications


class Command(BaseCommand):
    help = 'Classify unclassified transactions using Google Gemini AI'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show suggestions without applying them',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write('DRY RUN - no changes will be saved\n')

        try:
            classified, skipped, details = apply_ai_classifications(dry_run=dry_run)
        except Exception as e:
            self.stderr.write(f'Error: {e}')
            return

        for desc, suggestion, applied in details:
            status = 'APPLIED' if applied else 'SKIPPED'
            if dry_run and applied:
                status = 'WOULD APPLY'
            self.stdout.write(f'  [{status:12s}] {suggestion:25s} <- {desc[:50]}')

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Done! Classified: {classified}, Skipped: {skipped}'
        ))
