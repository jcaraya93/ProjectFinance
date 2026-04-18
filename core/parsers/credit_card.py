import csv
import io
import re
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .base import BaseParser, ParsedLedger, ParsedStatement, ParsedTransaction
from core.instrumentation import tracer, parser_files_processed, parser_duration

FOOTER_KEYWORDS = [
    "REVERSION INTERES",
    "TASA MENSUAL",
    "MONEDA",
    "PUNTOS",
    "ASIGNADOS",
    "CURRENT Interest",
]

DATE_RE = re.compile(r'^\d{2}/\d{2}/\d{4}$')
TRAILING_SLASH_RE = re.compile(r'\\\s*[CU]\s*$')


def _parse_decimal(value: str) -> Decimal:
    if not value:
        return Decimal(0)
    cleaned = value.strip().replace(',', '')
    if not cleaned:
        return Decimal(0)
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return Decimal(0)


def _parse_date(value: str):
    value = value.strip()
    if not value or not DATE_RE.match(value):
        return None
    try:
        return datetime.strptime(value, '%d/%m/%Y').date()
    except ValueError:
        return None


def _clean_description(desc: str) -> str:
    desc = TRAILING_SLASH_RE.sub('', desc)
    desc = desc.rstrip('\\').strip()
    return ' '.join(desc.split())


def _is_footer_row(description: str) -> bool:
    upper = description.upper()
    return any(kw.upper() in upper for kw in FOOTER_KEYWORDS)


def _parse_points(description: str) -> tuple:
    """Extract assigned and redeemable points from ASIGNADOS row."""
    m = re.search(r'ASIGNADOS\s+(\d+)\s+REDIMIBLE\s+(\d+)', description.upper())
    if m:
        return int(m.group(1)), int(m.group(2))
    return 0, 0


class CreditCardParser(BaseParser):

    def parse(self, file_content: str) -> ParsedStatement:
        with tracer.start_as_current_span("parser.credit_card.parse") as span:
            t0 = time.monotonic()
            span.set_attribute("parser.type", "credit_card")
            span.set_attribute("parser.content_length", len(file_content))

            reader = csv.reader(io.StringIO(file_content.lstrip('\ufeff')))
            rows = list(reader)

            statement = ParsedStatement()
            crc_ledger = ParsedLedger(currency='CRC')
            usd_ledger = ParsedLedger(currency='USD')

            # Row 2 (index 1): account values
            if len(rows) > 1:
                acct = rows[1]
                statement.card_number = acct[0].strip() if len(acct) > 0 else ''
                statement.card_holder = acct[1].strip() if len(acct) > 1 else ''
                if len(acct) > 2:
                    statement.statement_date = _parse_date(acct[2])

            # Row 4 (index 3): previous balance
            if len(rows) > 3:
                pb = rows[3]
                crc_ledger.previous_balance = _parse_decimal(pb[-2]) if len(pb) >= 2 else Decimal(0)
                usd_ledger.previous_balance = _parse_decimal(pb[-1]) if len(pb) >= 1 else Decimal(0)

            # Track sums for validation
            sum_local = Decimal(0)
            sum_dollars = Decimal(0)
            last_date = statement.statement_date

            for i in range(5, len(rows)):
                row = rows[i]
                if not row or all(c.strip() == '' for c in row):
                    continue

                desc_raw = row[1].strip() if len(row) > 1 else ''
                description = _clean_description(desc_raw)

                local_amt = _parse_decimal(row[-2]) if len(row) >= 2 else Decimal(0)
                dollar_amt = _parse_decimal(row[-1]) if len(row) >= 1 else Decimal(0)

                # Footer/metadata rows — now treated as transactions except points/rates
                if _is_footer_row(description):
                    self._handle_footer(
                        statement, crc_ledger, usd_ledger,
                        description, local_amt, dollar_amt, last_date
                    )
                    sum_local += local_amt
                    sum_dollars += dollar_amt
                    continue

                if local_amt == 0 and dollar_amt == 0 and not description:
                    continue

                # Final summary line (interest + balance at cutoff)
                final_result = self._try_parse_final_line(row, crc_ledger, usd_ledger, last_date)
                if final_result:
                    sum_local += final_result[0]
                    sum_dollars += final_result[1]
                    continue

                # Parse date
                row_date = _parse_date(row[0]) if row else None
                if row_date:
                    last_date = row_date
                txn_date = row_date or last_date
                if txn_date is None:
                    continue

                if local_amt != 0:
                    crc_ledger.transactions.append(ParsedTransaction(
                        date=txn_date, description=description,
                        amount=local_amt,
                    ))
                    sum_local += local_amt

                if dollar_amt != 0:
                    usd_ledger.transactions.append(ParsedTransaction(
                        date=txn_date, description=description,
                        amount=dollar_amt,
                    ))
                    sum_dollars += dollar_amt

            statement.ledgers = [crc_ledger, usd_ledger]
            self._validate(statement, crc_ledger, sum_local, 'CRC')
            self._validate(statement, usd_ledger, sum_dollars, 'USD')

            txn_count = len(crc_ledger.transactions) + len(usd_ledger.transactions)
            elapsed_ms = (time.monotonic() - t0) * 1000
            span.set_attribute("parser.transaction_count", txn_count)
            span.set_attribute("parser.crc_count", len(crc_ledger.transactions))
            span.set_attribute("parser.usd_count", len(usd_ledger.transactions))
            span.set_attribute("parser.warning_count", len(statement.warnings))
            parser_duration.record(elapsed_ms, {"parser": "credit_card"})
            status = "warning" if statement.warnings else "success"
            parser_files_processed.add(1, {"parser": "credit_card", "status": status})

            return statement

    def _handle_footer(self, statement, crc_ledger, usd_ledger,
                       description, local_amt, dollar_amt, last_date):
        upper = description.upper()
        if 'REVERSION INTERES' in upper:
            txn_date = last_date or statement.statement_date
            if txn_date:
                if local_amt != 0:
                    crc_ledger.transactions.append(ParsedTransaction(
                        date=txn_date, description=description, amount=local_amt,
                    ))
                if dollar_amt != 0:
                    usd_ledger.transactions.append(ParsedTransaction(
                        date=txn_date, description=description, amount=dollar_amt,
                    ))
        elif 'ASIGNADOS' in upper:
            assigned, redeemable = _parse_points(description)
            statement.points_assigned = assigned
            statement.points_redeemable = redeemable

    def _try_parse_final_line(self, row, crc_ledger, usd_ledger, last_date):
        """Returns (local_amt, dollar_amt) if final line, else None."""
        if len(row) < 4:
            return None
        if _parse_date(row[0]) is not None:
            return None

        vals = []
        for cell in row[-4:]:
            try:
                v = Decimal(cell.strip().replace(',', '')) if cell.strip() else None
                vals.append(v)
            except InvalidOperation:
                return None

        if any(v is None for v in vals):
            return None

        int_local, int_dollars, bal_local, bal_dollars = vals
        crc_ledger.balance_at_cutoff = bal_local
        usd_ledger.balance_at_cutoff = bal_dollars

        # Add current interest as transactions
        txn_date = last_date or None
        if txn_date:
            if int_local != 0:
                crc_ledger.transactions.append(ParsedTransaction(
                    date=txn_date, description='INTERESES DEL MES', amount=int_local,
                ))
            if int_dollars != 0:
                usd_ledger.transactions.append(ParsedTransaction(
                    date=txn_date, description='INTERESES DEL MES', amount=int_dollars,
                ))
        return (int_local, int_dollars)

    def _validate(self, statement, ledger, txn_sum, label):
        expected = ledger.previous_balance + txn_sum
        delta = abs(expected - ledger.balance_at_cutoff)
        if delta > Decimal('0.01'):
            statement.warnings.append(
                f'{label} validation failed: previous({ledger.previous_balance}) + '
                f'transactions({txn_sum}) = '
                f'{expected}, but balance_at_cutoff = {ledger.balance_at_cutoff} '
                f'(delta={delta})'
            )
