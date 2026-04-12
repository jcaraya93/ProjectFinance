import csv
import io
import re
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .base import BaseParser, ParsedLedger, ParsedStatement, ParsedTransaction
from transactions.instrumentation import tracer, parser_files_processed, parser_duration

DATE_RE = re.compile(r'^\d{2}/\d{2}/\d{4}$')


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
    desc = desc.replace('_', ' ')
    desc = desc.strip()
    return ' '.join(desc.split())


def _read_csv(file_content: str) -> list:
    """Try utf-8-sig first (handles BOM), fall back to latin-1 re-decode."""
    content = file_content.lstrip('\ufeff')
    reader = csv.reader(io.StringIO(content))
    return list(reader)


class DebitCardParser(BaseParser):

    def parse(self, file_content: str) -> ParsedStatement:
        with tracer.start_as_current_span("parser.debit_card.parse") as span:
            t0 = time.monotonic()
            span.set_attribute("parser.type", "debit_card")
            span.set_attribute("parser.content_length", len(file_content))

            rows = _read_csv(file_content)
            statement = ParsedStatement()

            if len(rows) < 2:
                statement.warnings.append('File has fewer than 2 rows; cannot parse account info')
                parser_files_processed.add(1, {"parser": "debit_card", "status": "error"})
                return statement

            # --- Row 2 (index 1): account info ---
            acct = rows[1]
            statement.card_number = acct[2].strip() if len(acct) > 2 else ''
            statement.card_holder = acct[1].strip() if len(acct) > 1 else ''
            statement.client_number = acct[0].strip() if len(acct) > 0 else ''

            currency = acct[3].strip().upper() if len(acct) > 3 else 'CRC'
            ledger = ParsedLedger(currency=currency)
            ledger.previous_balance = _parse_decimal(acct[4]) if len(acct) > 4 else Decimal(0)

            # --- Transactions (starting at index 5) ---
            txn_sum = Decimal(0)
            last_balance = Decimal(0)
            last_date = None

            for i in range(5, len(rows)):
                row = rows[i]

                if not row or all(c.strip() == '' for c in row):
                    break
                if any('Resumen de Estado Bancario' in c for c in row):
                    break

                txn_date = _parse_date(row[0]) if len(row) > 0 else None
                if txn_date is None:
                    continue

                reference = row[1].strip() if len(row) > 1 else ''
                code = row[2].strip() if len(row) > 2 else ''
                description = _clean_description(row[3]) if len(row) > 3 else ''
                debit = _parse_decimal(row[4]) if len(row) > 4 else Decimal(0)
                credit = _parse_decimal(row[5]) if len(row) > 5 else Decimal(0)
                balance = _parse_decimal(row[6]) if len(row) > 6 else Decimal(0)

                if credit > 0:
                    amount = credit
                elif debit > 0:
                    amount = -debit
                else:
                    amount = Decimal(0)

                txn_sum += amount
                last_balance = balance
                last_date = txn_date

                ledger.transactions.append(ParsedTransaction(
                    date=txn_date,
                    description=description,
                    amount=amount,
                    account_metadata={'transaction_code': code, 'reference_number': reference},
                ))

            ledger.balance_at_cutoff = last_balance

            if last_date:
                statement.statement_date = last_date

            statement.ledgers = [ledger]

            # --- Validation ---
            expected = ledger.previous_balance + txn_sum
            delta = abs(expected - ledger.balance_at_cutoff)
            if delta > Decimal('0.01'):
                statement.warnings.append(
                    f'{currency} validation failed: previous({ledger.previous_balance}) + '
                    f'transactions({txn_sum}) = '
                    f'{expected}, but balance_at_cutoff = {ledger.balance_at_cutoff} '
                    f'(delta={delta})'
                )

            elapsed_ms = (time.monotonic() - t0) * 1000
            span.set_attribute("parser.transaction_count", len(ledger.transactions))
            span.set_attribute("parser.currency", currency)
            span.set_attribute("parser.warning_count", len(statement.warnings))
            parser_duration.record(elapsed_ms, {"parser": "debit_card"})
            status = "warning" if statement.warnings else "success"
            parser_files_processed.add(1, {"parser": "debit_card", "status": status})

            return statement
