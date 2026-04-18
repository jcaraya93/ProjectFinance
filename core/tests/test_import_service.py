"""Layer 2 tests for core.services.import_service (with DB)."""
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
from core.models import (
    CreditAccount, DebitAccount, StatementImport, CurrencyLedger,
    RawTransaction, LogicalTransaction, ClassificationRule,
)
from core.services.import_service import detect_card_type, import_statement

FIXTURES = Path(__file__).parent / 'fixtures'


# ── detect_card_type (pure functions, no DB needed) ───────────


class TestDetectCardType:
    def test_credit(self, credit_csv):
        assert detect_card_type(credit_csv) == 'credit'

    def test_debit(self, debit_csv):
        assert detect_card_type(debit_csv) == 'debit'


# ── import_statement (DB tests) ──────────────────────────────

@pytest.mark.django_db
@patch('core.services.import_service.fetch_rates')
class TestImportCredit:
    def test_creates_records(self, mock_fetch, user, exchange_rates, credit_csv):
        result = import_statement(credit_csv, 'credit.csv', 'hash-credit-1', user)

        assert not result.skipped
        assert result.card_type == 'credit'
        assert result.transaction_count > 0
        assert CreditAccount.objects.filter(user=user).count() == 1
        assert StatementImport.objects.filter(user=user).count() == 1
        assert CurrencyLedger.objects.filter(user=user).count() >= 1
        assert RawTransaction.objects.filter(user=user).count() == result.transaction_count
        assert LogicalTransaction.objects.filter(user=user).count() == result.transaction_count

    def test_duplicate_detection(self, mock_fetch, user, exchange_rates, credit_csv):
        r1 = import_statement(credit_csv, 'credit.csv', 'hash-dup', user)
        assert not r1.skipped

        r2 = import_statement(credit_csv, 'credit.csv', 'hash-dup', user)
        assert r2.skipped
        assert r2.skip_reason == 'duplicate'

    def test_account_reuse(self, mock_fetch, user, exchange_rates, credit_csv):
        import_statement(credit_csv, 'credit.csv', 'hash-a', user)
        import_statement(credit_csv, 'credit2.csv', 'hash-b', user)

        assert CreditAccount.objects.filter(user=user).count() == 1
        assert StatementImport.objects.filter(user=user).count() == 2

    def test_points_saved(self, mock_fetch, user, exchange_rates, credit_csv):
        import_statement(credit_csv, 'credit.csv', 'hash-pts', user)
        stmt = StatementImport.objects.get(user=user)
        assert stmt.points_assigned == 50000

    def test_unclassified_gets_default(self, mock_fetch, user, exchange_rates, credit_csv):
        result = import_statement(credit_csv, 'credit.csv', 'hash-unclass', user)
        assert not result.skipped

        for txn in LogicalTransaction.objects.filter(user=user):
            assert txn.category is not None
            assert txn.category.name == 'Default'

    def test_classification_during_import(
        self, mock_fetch, user, expense_category, exchange_rates, credit_csv
    ):
        ClassificationRule.objects.create(
            category=expense_category, user=user, description='CAFE CENTRAL',
        )
        result = import_statement(credit_csv, 'credit.csv', 'hash-classify', user)
        assert not result.skipped

        rule_txns = LogicalTransaction.objects.filter(
            user=user, classification_method='rule',
        )
        assert rule_txns.exists()

    def test_currency_conversion_during_import(
        self, mock_fetch, user, exchange_rates, credit_csv
    ):
        result = import_statement(credit_csv, 'credit.csv', 'hash-conv', user)
        assert not result.skipped
        assert result.converted_count > 0

        txn = LogicalTransaction.objects.filter(user=user, amount_crc__isnull=False).first()
        assert txn is not None
        assert txn.amount_crc is not None or txn.amount_usd is not None


@pytest.mark.django_db
@patch('core.services.import_service.fetch_rates')
class TestImportDebit:
    def test_creates_records(self, mock_fetch, user, exchange_rates, debit_csv):
        result = import_statement(debit_csv, 'debit.csv', 'hash-debit-1', user)

        assert not result.skipped
        assert result.card_type == 'debit'
        assert result.transaction_count > 0
        assert DebitAccount.objects.filter(user=user).count() == 1
        assert StatementImport.objects.filter(user=user).count() == 1
        assert RawTransaction.objects.filter(user=user).count() == result.transaction_count
        assert LogicalTransaction.objects.filter(user=user).count() == result.transaction_count


@pytest.mark.django_db
@patch('core.services.import_service.fetch_rates')
class TestImportEdgeCases:
    def test_empty_file_skipped(self, mock_fetch, user):
        result = import_statement('', 'empty.csv', 'hash-empty', user)
        assert result.skipped
        assert result.skip_reason == 'no_transactions'
