"""Layer 2 tests for core.services.exchange_rates (with DB)."""
from datetime import date
from decimal import Decimal

import pytest
from core.models import ExchangeRate
from core.services.exchange_rates import get_rate, convert_transaction
from core.tests.factories import (
    CreditAccountFactory, StatementImportFactory, CurrencyLedgerFactory,
    RawTransactionFactory, LogicalTransactionFactory, ExchangeRateFactory,
)


@pytest.mark.django_db
class TestGetRate:
    def test_exact_date(self):
        ExchangeRateFactory(date=date(2025, 2, 10), usd_to_crc=Decimal('512.00'))
        assert get_rate(date(2025, 2, 10)) == Decimal('512.00')

    def test_fallback_previous(self):
        ExchangeRateFactory(date=date(2025, 2, 8), usd_to_crc=Decimal('511.00'))
        assert get_rate(date(2025, 2, 10)) == Decimal('511.00')

    def test_fallback_future(self):
        ExchangeRateFactory(date=date(2025, 2, 15), usd_to_crc=Decimal('513.00'))
        assert get_rate(date(2025, 2, 10)) == Decimal('513.00')

    def test_no_rates(self):
        assert get_rate(date(2025, 2, 10)) is None


@pytest.mark.django_db
class TestConvertTransaction:
    def _make_chain(self, user, currency, txn_date, amount):
        """Build the full object chain needed for convert_transaction."""
        acct = CreditAccountFactory(user=user)
        stmt = StatementImportFactory(account=acct, user=user)
        ledger = CurrencyLedgerFactory(
            statement_import=stmt, user=user, currency=currency,
        )
        raw = RawTransactionFactory(
            ledger=ledger, user=user, date=txn_date, amount=amount,
        )
        return LogicalTransactionFactory(
            raw_transaction=raw, user=user,
            date=txn_date, amount=amount,
        )

    def test_convert_crc_transaction(self, user):
        ExchangeRateFactory(date=date(2025, 2, 1), usd_to_crc=Decimal('510.50'))
        txn = self._make_chain(user, 'CRC', date(2025, 2, 1), Decimal('5000.00'))

        assert convert_transaction(txn) is True
        assert txn.amount_crc == Decimal('5000.00')
        expected_usd = Decimal('5000.00') / Decimal('510.50')
        assert abs(txn.amount_usd - expected_usd) < Decimal('0.01')

    def test_convert_usd_transaction(self, user):
        ExchangeRateFactory(date=date(2025, 2, 1), usd_to_crc=Decimal('510.50'))
        txn = self._make_chain(user, 'USD', date(2025, 2, 1), Decimal('29.99'))

        assert convert_transaction(txn) is True
        assert txn.amount_usd == Decimal('29.99')
        expected_crc = Decimal('29.99') * Decimal('510.50')
        assert abs(txn.amount_crc - expected_crc) < Decimal('0.01')
