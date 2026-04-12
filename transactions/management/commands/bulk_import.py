import os
from django.core.management.base import BaseCommand
from django.conf import settings
from transactions.models import StatementImport, CurrencyLedger, RawTransaction, LogicalTransaction, Transaction, Account, CreditAccount, DebitAccount, Category, User
from transactions.parsers.credit_card import CreditCardParser
from transactions.parsers.debit_card import DebitCardParser
from transactions.services.classifier import classify_transaction
from transactions.services.exchange_rates import fetch_rates, convert_transaction


class Command(BaseCommand):
    help = 'Bulk import all CSV files from the Data/ directory'

    def add_arguments(self, parser):
        parser.add_argument(
            '--data-dir',
            default=os.path.join(settings.BASE_DIR, 'Data'),
            help='Path to the Data directory',
        )
        parser.add_argument('--user', type=str, help='User email to assign imports to')

    def handle(self, *args, **options):
        data_dir = options['data_dir']
        user_email = options.get('user')
        if user_email:
            user = User.objects.get(email=user_email)
        else:
            user = User.objects.first()
            if not user:
                self.stderr.write('No users exist. Create one first or pass --user.')
                return

        if not os.path.isdir(data_dir):
            self.stderr.write(f'Data directory not found: {data_dir}')
            return

        total_imported = 0
        total_skipped = 0
        all_warnings = []
        unclassified = Category.get_unclassified(user)

        for folder_name in sorted(os.listdir(data_dir)):
            folder_path = os.path.join(data_dir, folder_name)
            if not os.path.isdir(folder_path):
                continue

            # Determine card type from folder name
            if 'credit' in folder_name.lower():
                card_type = 'credit'
                parser = CreditCardParser()
            elif 'debit' in folder_name.lower():
                card_type = 'debit'
                parser = DebitCardParser()
            else:
                self.stdout.write(f'Skipping {folder_name} (no parser for this type yet)')
                continue

            for csv_file in sorted(os.listdir(folder_path)):
                if not csv_file.endswith('.csv'):
                    continue

                file_path = os.path.join(folder_path, csv_file)
                display_name = f'{folder_name}/{csv_file}'

                # Skip if already imported
                account_type = 'credit_account' if card_type == 'credit' else 'debit_account'
                if StatementImport.objects.filter(user=user, filename=display_name, account__account_type=account_type).exists():
                    self.stdout.write(f'  Skipping {display_name} (already imported)')
                    total_skipped += 1
                    continue

                self.stdout.write(f'  Importing {display_name}...')

                try:
                    with open(file_path, 'r', encoding='utf-8-sig') as f:
                        content = f.read()
                except UnicodeDecodeError:
                    with open(file_path, 'r', encoding='latin-1') as f:
                        content = f.read()

                try:
                    parsed = parser.parse(content)
                except Exception as e:
                    self.stderr.write(f'  ERROR parsing {display_name}: {e}')
                    continue

                # Auto-create or get Account
                if card_type == 'credit':
                    account, _ = CreditAccount.objects.get_or_create(
                        user=user, card_number_hash=CreditAccount.hash_card_number(parsed.card_number),
                        defaults={'card_holder': parsed.card_holder, 'card_number_last4': parsed.card_number[-4:]},
                    )
                else:
                    account, _ = DebitAccount.objects.get_or_create(
                        user=user, iban=parsed.card_number,
                        defaults={
                            'card_holder': parsed.card_holder,
                            'client_number': getattr(parsed, 'client_number', ''),
                        },
                    )

                stmt_import = StatementImport.objects.create(
                    account=account, user=user,
                    filename=display_name,
                    statement_date=parsed.statement_date,
                    points_assigned=parsed.points_assigned,
                    points_redeemable=parsed.points_redeemable,
                )

                file_txn_count = 0
                for pl in parsed.ledgers:
                    ledger = CurrencyLedger.objects.create(
                        statement_import=stmt_import, user=user,
                        currency=pl.currency,
                        previous_balance=pl.previous_balance,
                        balance_at_cutoff=pl.balance_at_cutoff,
                    )
                    for pt in pl.transactions:
                        raw = RawTransaction.objects.create(
                            date=pt.date, description=pt.description,
                            amount=pt.amount, ledger=ledger,
                            user=user, account_metadata=pt.account_metadata,
                        )
                        txn = LogicalTransaction.objects.create(
                            raw_transaction=raw, user=user,
                            date=pt.date, description=pt.description,
                            amount=pt.amount, category=unclassified,
                        )
                        cat, rule_obj = classify_transaction(txn)
                        if rule_obj:
                            txn.category = cat
                            txn.matched_rule = rule_obj
                            txn.classification_method = 'rule'
                            txn.save(update_fields=['category', 'matched_rule', 'classification_method'])
                    file_txn_count += len(pl.transactions)

                # Fetch exchange rates and convert transactions
                all_dates = [t.date for pl in parsed.ledgers for t in pl.transactions]
                if all_dates:
                    try:
                        fetch_rates(min(all_dates), max(all_dates))
                    except Exception as e:
                        self.stdout.write(self.style.WARNING(f'    Warning: exchange rates unavailable: {e}'))

                for ledger in stmt_import.ledgers.all():
                    for raw in ledger.raw_transactions.all():
                        for txn in raw.logical_transactions.all():
                            if convert_transaction(txn):
                                txn.save(update_fields=['amount_crc', 'amount_usd'])

                total_imported += file_txn_count
                self.stdout.write(f'    -> {file_txn_count} transactions')

                for w in parsed.warnings:
                    all_warnings.append(f'{display_name}: {w}')
                    self.stdout.write(self.style.WARNING(f'    WARNING: {w}'))

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Done! Imported {total_imported} transactions, skipped {total_skipped} files.'
        ))
        if all_warnings:
            self.stdout.write(self.style.WARNING(f'{len(all_warnings)} warning(s):'))
            for w in all_warnings:
                self.stdout.write(f'  - {w}')
