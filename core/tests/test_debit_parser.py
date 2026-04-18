import pytest
from datetime import date
from decimal import Decimal
from pathlib import Path

from core.parsers.debit_card import (
    _parse_decimal, _parse_date, _clean_description, DebitCardParser,
)

FIXTURES = Path(__file__).parent / 'fixtures'


class TestDebitParseDecimal:
    def test_normal(self):
        assert _parse_decimal('50000.00') == Decimal('50000.00')

    def test_with_comma(self):
        assert _parse_decimal('1,234.56') == Decimal('1234.56')

    def test_empty(self):
        assert _parse_decimal('') == Decimal(0)

    def test_invalid(self):
        assert _parse_decimal('abc') == Decimal(0)


class TestDebitCleanDescription:
    def test_underscores_to_spaces(self):
        assert _clean_description('SINPE_Movil_Sin_Descripcion') == 'SINPE Movil Sin Descripcion'

    def test_extra_spaces(self):
        assert _clean_description('SINPE MOVIL  Pago   Alquiler') == 'SINPE MOVIL Pago Alquiler'

    def test_normal(self):
        assert _clean_description('INTERESES') == 'INTERESES'


class TestDebitCardParser:
    @pytest.fixture
    def parser(self):
        return DebitCardParser()

    @pytest.fixture
    def basic_csv(self):
        return (FIXTURES / 'debit_basic.csv').read_text(encoding='utf-8')

    def test_iban(self, parser, basic_csv):
        result = parser.parse(basic_csv)
        assert result.card_number == 'CR61010200001234567890'

    def test_card_holder(self, parser, basic_csv):
        result = parser.parse(basic_csv)
        assert result.card_holder == 'MARIA FERNANDEZ LOPEZ'

    def test_currency(self, parser, basic_csv):
        result = parser.parse(basic_csv)
        assert len(result.ledgers) == 1
        assert result.ledgers[0].currency == 'CRC'

    def test_transaction_count(self, parser, basic_csv):
        result = parser.parse(basic_csv)
        assert len(result.ledgers[0].transactions) == 5

    def test_debit_is_negative(self, parser, basic_csv):
        """Debit amounts should be negative."""
        result = parser.parse(basic_csv)
        ledger = result.ledgers[0]
        rent = [t for t in ledger.transactions if 'Alquiler' in t.description]
        assert len(rent) == 1
        assert rent[0].amount == Decimal('-50000.00')

    def test_credit_is_positive(self, parser, basic_csv):
        """Credit amounts should be positive."""
        result = parser.parse(basic_csv)
        ledger = result.ledgers[0]
        salary = [t for t in ledger.transactions if 'SALARIO' in t.description]
        assert len(salary) == 1
        assert salary[0].amount == Decimal('300000.00')

    def test_metadata_extraction(self, parser, basic_csv):
        """Each transaction should have transaction_code and reference_number."""
        result = parser.parse(basic_csv)
        txn = result.ledgers[0].transactions[0]
        assert 'transaction_code' in txn.account_metadata
        assert 'reference_number' in txn.account_metadata
        assert txn.account_metadata['transaction_code'] == 'TF'
        assert txn.account_metadata['reference_number'] == '900123456'

    def test_stops_at_summary(self, parser, basic_csv):
        """Parser should stop at empty line before 'Resumen de Estado Bancario'."""
        result = parser.parse(basic_csv)
        all_descs = [t.description for t in result.ledgers[0].transactions]
        assert not any('Total' in d for d in all_descs)

    def test_previous_balance(self, parser, basic_csv):
        result = parser.parse(basic_csv)
        assert result.ledgers[0].previous_balance == Decimal('100000.00')

    def test_balance_at_cutoff(self, parser, basic_csv):
        result = parser.parse(basic_csv)
        assert result.ledgers[0].balance_at_cutoff == Decimal('250000.00')

    def test_balance_validation_passes(self, parser, basic_csv):
        result = parser.parse(basic_csv)
        assert result.warnings == [], f"Unexpected warnings: {result.warnings}"

    def test_statement_date(self, parser, basic_csv):
        """Statement date should be the last transaction date."""
        result = parser.parse(basic_csv)
        assert result.statement_date == date(2025, 2, 20)

    def test_too_few_rows(self, parser):
        result = parser.parse('just one line\n')
        assert len(result.warnings) > 0
        assert 'fewer than 2 rows' in result.warnings[0]

    def test_empty_file(self, parser):
        result = parser.parse('')
        assert len(result.warnings) > 0
