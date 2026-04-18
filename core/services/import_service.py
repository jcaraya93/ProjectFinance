"""
Statement import service — single-pass pipeline with bulk DB operations.

Usage:
    from core.services.import_service import import_statement, ImportResult
    result = import_statement(content, filename, file_hash, user)
"""

import hashlib
import logging
import time
from dataclasses import dataclass, field

from django.db import transaction

from core.models import (
    StatementImport, CurrencyLedger, RawTransaction, LogicalTransaction,
    Category, CreditAccount, DebitAccount, ExchangeRate,
)
from core.parsers.credit_card import CreditCardParser
from core.parsers.debit_card import DebitCardParser
from core.services.yaml_classifier import load_rules, _match_rule, _rule_phase, reload_rules
from core.services.exchange_rates import fetch_rates, get_rate
from core.instrumentation import (
    tracer, transactions_imported, upload_duration,
    classification_result, classification_duration,
)

logger = logging.getLogger(__name__)


@dataclass
class ImportResult:
    filename: str
    card_type: str
    transaction_count: int = 0
    classified_count: int = 0
    converted_count: int = 0
    warnings: list = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ''


def detect_card_type(content: str) -> str:
    """Auto-detect whether a CSV is a credit card or debit card statement."""
    first_line = content.split('\n', 1)[0].strip()
    if first_line.startswith('Pro') or '5466-' in content[:500]:
        return 'credit'
    return 'debit'


def _classify_in_memory(txn_obj, rules, unclassified):
    """Classify a LogicalTransaction object in memory using pre-loaded rules.

    Sets category, matched_rule, and classification_method directly on the
    object without saving to the database.
    """
    desc_upper = txn_obj.description.upper()
    metadata = txn_obj._import_metadata
    amount = txn_obj.amount
    account_type = txn_obj._import_account_type

    for phase in (0, 1, 2):
        best_rule = None
        best_specificity = 0

        for rule_obj in rules:
            if _rule_phase(rule_obj) != phase:
                continue
            flat = rule_obj.to_flat_dict()
            score = _match_rule(flat, desc_upper, metadata, amount, account_type)
            if score == 0:
                continue
            desc_len = len(flat.get('description', ''))
            non_desc = score - (1 if 'description' in flat else 0)
            specificity = (desc_len * 10) + non_desc
            if specificity > best_specificity:
                best_rule = rule_obj
                best_specificity = specificity

        if best_rule:
            txn_obj.category = best_rule.category
            txn_obj.matched_rule = best_rule
            txn_obj.classification_method = 'rule'
            return True

    txn_obj.category = unclassified
    txn_obj.classification_method = 'unclassified'
    return False


def _convert_in_memory(txn_obj, currency, rates_cache):
    """Convert currency amounts in memory using a pre-loaded rates dict."""
    rate = rates_cache.get(txn_obj.date)
    if rate is None:
        return False

    if currency == 'CRC':
        txn_obj.amount_crc = txn_obj.amount
        txn_obj.amount_usd = txn_obj.amount / rate
    elif currency == 'USD':
        txn_obj.amount_usd = txn_obj.amount
        txn_obj.amount_crc = txn_obj.amount * rate
    return True


def _build_rates_cache(start_date, end_date):
    """Pre-fetch all exchange rates for a date range into a dict."""
    try:
        fetch_rates(start_date, end_date)
    except Exception as e:
        logger.warning('Exchange rates unavailable: %s', e)

    rates = ExchangeRate.objects.filter(
        date__gte=start_date, date__lte=end_date
    ).values_list('date', 'usd_to_crc')

    cache = {d: r for d, r in rates}

    # Add fallback: for dates not in cache, find nearest
    if cache:
        all_rates = list(ExchangeRate.objects.order_by('date').values_list('date', 'usd_to_crc'))
        all_rates_dict = dict(all_rates)
        from datetime import timedelta
        d = start_date
        while d <= end_date:
            if d not in cache:
                rate = get_rate(d)
                if rate is not None:
                    cache[d] = rate
            d += timedelta(days=1)

    return cache


def import_statement(content: str, filename: str, file_hash: str, user) -> ImportResult:
    """Import a single CSV statement: parse → classify → convert → bulk write.

    Each call is wrapped in a database transaction for atomicity.
    """
    with tracer.start_as_current_span("import_service.import_statement") as span:
        t0 = time.monotonic()

        card_type = detect_card_type(content)
        span.set_attribute("import.card_type", card_type)
        span.set_attribute("import.filename", filename)

        result = ImportResult(filename=filename, card_type=card_type)

        # --- Parse ---
        parser = CreditCardParser() if card_type == 'credit' else DebitCardParser()
        parsed = parser.parse(content)

        txn_count = sum(len(led.transactions) for led in parsed.ledgers)
        if txn_count == 0:
            result.skipped = True
            result.skip_reason = 'no_transactions'
            return result

        # --- Duplicate check ---
        if StatementImport.objects.filter(user=user, file_hash=file_hash).exists():
            result.skipped = True
            result.skip_reason = 'duplicate'
            return result

        result.warnings = list(parsed.warnings)

        # --- Pre-load classification rules ---
        reload_rules()
        rules = load_rules()
        unclassified = Category.get_unclassified(user)

        # --- Pre-fetch exchange rates ---
        all_dates = [t.date for pl in parsed.ledgers for t in pl.transactions]
        rates_cache = _build_rates_cache(min(all_dates), max(all_dates))

        # --- Atomic DB write ---
        with transaction.atomic():
            # Create account
            if card_type == 'credit':
                account, _ = CreditAccount.objects.get_or_create(
                    user=user,
                    card_number_hash=CreditAccount.hash_card_number(parsed.card_number),
                    defaults={
                        'card_holder': parsed.card_holder,
                        'card_number_last4': parsed.card_number[-4:],
                    },
                )
            else:
                account, _ = DebitAccount.objects.get_or_create(
                    user=user, iban=parsed.card_number,
                    defaults={
                        'card_holder': parsed.card_holder,
                        'client_number': getattr(parsed, 'client_number', ''),
                    },
                )

            # Create statement
            stmt = StatementImport.objects.create(
                account=account, user=user, filename=filename, file_hash=file_hash,
                statement_date=parsed.statement_date,
                points_assigned=parsed.points_assigned,
                points_redeemable=parsed.points_redeemable,
            )

            account_type = account.account_type
            classified = 0
            converted = 0

            for pl in parsed.ledgers:
                ledger = CurrencyLedger.objects.create(
                    statement_import=stmt, user=user, currency=pl.currency,
                    previous_balance=pl.previous_balance,
                    balance_at_cutoff=pl.balance_at_cutoff,
                )

                raw_objects = []
                for pt in pl.transactions:
                    raw_objects.append(RawTransaction(
                        date=pt.date, description=pt.description, amount=pt.amount,
                        ledger=ledger, user=user, account_metadata=pt.account_metadata,
                    ))

                RawTransaction.objects.bulk_create(raw_objects)

                # Build logical transactions in memory with classification + conversion
                logical_objects = []
                for raw in raw_objects:
                    txn = LogicalTransaction(
                        raw_transaction=raw, user=user,
                        date=raw.date, description=raw.description,
                        amount=raw.amount, category=unclassified,
                    )
                    # Attach temp attributes for in-memory classification
                    txn._import_metadata = raw.account_metadata or {}
                    txn._import_account_type = account_type

                    if _classify_in_memory(txn, rules, unclassified):
                        classified += 1

                    if _convert_in_memory(txn, pl.currency, rates_cache):
                        converted += 1

                    logical_objects.append(txn)

                LogicalTransaction.objects.bulk_create(logical_objects)

            result.transaction_count = txn_count
            result.classified_count = classified
            result.converted_count = converted

        # --- Instrumentation ---
        elapsed_ms = (time.monotonic() - t0) * 1000
        span.set_attribute("import.transaction_count", txn_count)
        span.set_attribute("import.classified_count", classified)
        span.set_attribute("import.converted_count", converted)
        span.set_attribute("import.duration_ms", elapsed_ms)
        upload_duration.record(elapsed_ms)
        transactions_imported.add(txn_count)
        classification_result.add(classified, {"method": "rule"})
        classification_result.add(txn_count - classified, {"method": "unclassified"})

        return result
