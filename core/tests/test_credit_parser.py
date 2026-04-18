import pytest
from datetime import date
from decimal import Decimal
from pathlib import Path

from core.parsers.credit_card import (
    _parse_decimal, _parse_date, _clean_description,
    _is_footer_row, _parse_points, CreditCardParser,
)

FIXTURES = Path(__file__).parent / 'fixtures'


class TestParseDecimal:
    def test_normal(self):
        assert _parse_decimal('1,234.56') == Decimal('1234.56')

    def test_negative(self):
        assert _parse_decimal('-200000.00') == Decimal('-200000.00')

    def test_empty(self):
        assert _parse_decimal('') == Decimal(0)

    def test_whitespace(self):
        assert _parse_decimal('  ') == Decimal(0)

    def test_invalid(self):
        assert _parse_decimal('abc') == Decimal(0)

    def test_no_comma(self):
        assert _parse_decimal('500.00') == Decimal('500.00')


class TestParseDate:
    def test_valid(self):
        assert _parse_date('15/03/2025') == date(2025, 3, 15)

    def test_invalid_format(self):
        assert _parse_date('2025-03-15') is None

    def test_empty(self):
        assert _parse_date('') is None

    def test_whitespace(self):
        assert _parse_date('  ') is None

    def test_invalid_date(self):
        assert _parse_date('32/13/2025') is None


class TestCleanDescription:
    def test_trailing_slash_c(self):
        # Internal backslashes remain; only trailing \C or \U is removed
        assert _clean_description('STARBUCKS CITYZEN\\    HEREDIA\\     C') == 'STARBUCKS CITYZEN\\ HEREDIA'

    def test_trailing_slash_u(self):
        assert _clean_description('NETFLIX.COM\\            866-579-7172\\U') == 'NETFLIX.COM\\ 866-579-7172'

    def test_extra_spaces(self):
        assert _clean_description('UBER   TRIP') == 'UBER TRIP'

    def test_normal(self):
        assert _clean_description('SU PAGO RECIBIDO GRACIAS') == 'SU PAGO RECIBIDO GRACIAS'

    def test_backslash_only(self):
        assert _clean_description('SOMETHING\\') == 'SOMETHING'


class TestIsFooterRow:
    def test_tasa_mensual(self):
        assert _is_footer_row('TASA MENSUAL INTERES CORRIENTE') is True

    def test_reversion(self):
        assert _is_footer_row('REVERSION INTERES CORRIENTES PERIODO') is True

    def test_puntos(self):
        assert _is_footer_row('PUNTOS CASH BACK PREMIUM') is True

    def test_asignados(self):
        assert _is_footer_row('ASIGNADOS    50000 REDIMIBLE     50000') is True

    def test_normal_description(self):
        assert _is_footer_row('STARBUCKS CITYZEN') is False

    def test_case_insensitive(self):
        assert _is_footer_row('tasa mensual') is True


class TestParsePoints:
    def test_normal(self):
        assert _parse_points('ASIGNADOS    50000 REDIMIBLE     50000') == (50000, 50000)

    def test_different_values(self):
        assert _parse_points('ASIGNADOS 103584 REDIMIBLE     103584') == (103584, 103584)

    def test_no_match(self):
        assert _parse_points('STARBUCKS') == (0, 0)


class TestCreditCardParser:
    @pytest.fixture
    def parser(self):
        return CreditCardParser()

    @pytest.fixture
    def basic_csv(self):
        return (FIXTURES / 'credit_basic.csv').read_text(encoding='utf-8')

    def test_card_number(self, parser, basic_csv):
        result = parser.parse(basic_csv)
        assert result.card_number == '4000-00**-****-1234'

    def test_card_holder(self, parser, basic_csv):
        result = parser.parse(basic_csv)
        assert result.card_holder == 'JUAN/PEREZ GARCIA'

    def test_statement_date(self, parser, basic_csv):
        result = parser.parse(basic_csv)
        assert result.statement_date == date(2025, 3, 15)

    def test_two_ledgers(self, parser, basic_csv):
        result = parser.parse(basic_csv)
        assert len(result.ledgers) == 2
        assert result.ledgers[0].currency == 'CRC'
        assert result.ledgers[1].currency == 'USD'

    def test_crc_transactions(self, parser, basic_csv):
        result = parser.parse(basic_csv)
        crc = result.ledgers[0]
        crc_descriptions = [t.description for t in crc.transactions]
        assert any('CAFE CENTRAL' in d for d in crc_descriptions)
        assert any('PAGO RECIBIDO' in d for d in crc_descriptions)
        assert any('REVERSION INTERES' in d for d in crc_descriptions)

    def test_usd_transactions(self, parser, basic_csv):
        result = parser.parse(basic_csv)
        usd = result.ledgers[1]
        usd_descriptions = [t.description for t in usd.transactions]
        assert any('AMAZON' in d for d in usd_descriptions)
        assert any('NETFLIX' in d for d in usd_descriptions)

    def test_payment_is_negative(self, parser, basic_csv):
        result = parser.parse(basic_csv)
        crc = result.ledgers[0]
        payments = [t for t in crc.transactions if 'PAGO RECIBIDO' in t.description]
        assert len(payments) >= 1
        assert payments[0].amount < 0

    def test_dual_currency_split(self, parser, basic_csv):
        """CRC amounts go to CRC ledger, USD amounts go to USD ledger."""
        result = parser.parse(basic_csv)
        crc = result.ledgers[0]
        usd = result.ledgers[1]
        crc_descs = [t.description for t in crc.transactions]
        usd_descs = [t.description for t in usd.transactions]
        assert not any('AMAZON' in d for d in crc_descs)
        assert any('AMAZON' in d for d in usd_descs)

    def test_balance_validation_passes(self, parser, basic_csv):
        result = parser.parse(basic_csv)
        assert result.warnings == [], f"Unexpected warnings: {result.warnings}"

    def test_points_extracted(self, parser, basic_csv):
        result = parser.parse(basic_csv)
        assert result.points_assigned == 50000
        assert result.points_redeemable == 50000

    def test_previous_balance(self, parser, basic_csv):
        result = parser.parse(basic_csv)
        assert result.ledgers[0].previous_balance == Decimal('500000.00')
        assert result.ledgers[1].previous_balance == Decimal('800.00')

    def test_balance_at_cutoff(self, parser, basic_csv):
        result = parser.parse(basic_csv)
        assert result.ledgers[0].balance_at_cutoff == Decimal('351910.00')
        assert result.ledgers[1].balance_at_cutoff == Decimal('338.98')

    def test_footer_not_in_transactions(self, parser, basic_csv):
        """TASA MENSUAL, MONEDA, PUNTOS should NOT appear as transactions."""
        result = parser.parse(basic_csv)
        all_descs = []
        for ledger in result.ledgers:
            all_descs.extend(t.description for t in ledger.transactions)
        assert not any('TASA MENSUAL' in d for d in all_descs)
        assert not any('MONEDA' in d for d in all_descs)

    def test_empty_file(self, parser):
        result = parser.parse('')
        assert len(result.ledgers) == 2
        total_txns = sum(len(l.transactions) for l in result.ledgers)
        assert total_txns == 0

    def test_interest_becomes_transaction(self, parser, basic_csv):
        """REVERSION INTERES rows are treated as transactions."""
        result = parser.parse(basic_csv)
        crc = result.ledgers[0]
        interest_txns = [t for t in crc.transactions if 'REVERSION INTERES' in t.description]
        assert len(interest_txns) == 1
        assert interest_txns[0].amount == Decimal('-5000.00')

    def test_usd_interest_added(self, parser, basic_csv):
        """Monthly interest (3.50 USD) becomes an INTERESES DEL MES transaction."""
        result = parser.parse(basic_csv)
        usd = result.ledgers[1]
        interest = [t for t in usd.transactions if 'INTERESES DEL MES' in t.description]
        assert len(interest) == 1
        assert interest[0].amount == Decimal('3.50')
