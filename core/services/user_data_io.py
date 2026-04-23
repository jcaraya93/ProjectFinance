"""
Export and import all data belonging to a single user as portable JSON.

export_user_data(user) → dict   – build a self-contained JSON-serialisable dict
import_user_data(user, data)    – restore from that dict into a fresh account
"""
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction

from core.models import (
    User, UserPreference, CategoryGroup, Category, ClassificationRule,
    Account, CreditAccount, DebitAccount, StatementImport, CurrencyLedger,
    RawTransaction, LogicalTransaction, ExchangeRate,
)

logger = logging.getLogger(__name__)

EXPORT_VERSION = 1


# ── helpers ───────────────────────────────────────────────────────────

def _dec(value):
    """Decimal → str for JSON (preserves precision)."""
    if value is None:
        return None
    return str(value)


def _to_decimal(value):
    """JSON str/number → Decimal, or None."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid decimal value: {value}")


def _to_date(value):
    """ISO date string → date object."""
    if isinstance(value, date):
        return value
    return datetime.strptime(value, '%Y-%m-%d').date()


# ── EXPORT ────────────────────────────────────────────────────────────

def export_user_data(user):
    """Build a complete JSON-serialisable dict of all data owned by *user*."""

    # Preferences
    prefs = {}
    try:
        prefs = {'transaction_columns': user.preferences.transaction_columns}
    except UserPreference.DoesNotExist:
        pass

    # Categories (skip the auto-created Default ones)
    categories = []
    for cat in Category.objects.filter(user=user).select_related('group').order_by('group__slug', 'name'):
        categories.append({
            'name': cat.name,
            'group_slug': cat.group.slug,
            'color': cat.color,
        })

    # Classification rules
    rules = []
    for rule in ClassificationRule.objects.filter(user=user).select_related('category__group'):
        r = {
            'category_name': rule.category.name,
            'category_group_slug': rule.category.group.slug,
            'description': rule.description,
            'account_type': rule.account_type,
            'amount_min': _dec(rule.amount_min),
            'amount_max': _dec(rule.amount_max),
            'metadata': rule.metadata,
            'detail': rule.detail,
        }
        rules.append(r)

    # Accounts → Statements → Ledgers → RawTxns → LogicalTxns
    accounts = []
    for acct in Account.objects.filter(user=user).order_by('pk'):
        acct_data = {
            'account_type': acct.account_type,
            'card_holder': acct.card_holder,
            'nickname': acct.nickname,
        }

        if acct.account_type == 'credit_account':
            try:
                credit = acct.creditaccount
                acct_data['credit_account'] = {
                    'card_number_hash': credit.card_number_hash,
                    'card_number_last4': credit.card_number_last4,
                }
            except CreditAccount.DoesNotExist:
                pass
        elif acct.account_type == 'debit_account':
            try:
                debit = acct.debitaccount
                acct_data['debit_account'] = {
                    'iban': debit.iban,
                    'client_number': debit.client_number,
                }
            except DebitAccount.DoesNotExist:
                pass

        # Statements for this account
        stmt_list = []
        for stmt in StatementImport.objects.filter(account=acct, user=user).order_by('pk'):
            stmt_data = {
                'filename': stmt.filename,
                'file_hash': stmt.file_hash,
                'statement_date': str(stmt.statement_date) if stmt.statement_date else None,
                'points_assigned': stmt.points_assigned,
                'points_redeemable': stmt.points_redeemable,
                'ledgers': [],
            }

            for ledger in CurrencyLedger.objects.filter(statement_import=stmt, user=user):
                ledger_data = {
                    'currency': ledger.currency,
                    'previous_balance': _dec(ledger.previous_balance),
                    'balance_at_cutoff': _dec(ledger.balance_at_cutoff),
                    'raw_transactions': [],
                }

                for raw in RawTransaction.objects.filter(ledger=ledger, user=user).order_by('pk'):
                    raw_data = {
                        'date': str(raw.date),
                        'description': raw.description,
                        'amount': _dec(raw.amount),
                        'account_metadata': raw.account_metadata,
                        'logical_transactions': [],
                    }

                    for ltxn in LogicalTransaction.objects.filter(raw_transaction=raw, user=user).order_by('pk'):
                        ltxn_data = {
                            'description': ltxn.description,
                            'amount': _dec(ltxn.amount),
                            'amount_crc': _dec(ltxn.amount_crc),
                            'amount_usd': _dec(ltxn.amount_usd),
                            'date': str(ltxn.date),
                            'category_name': ltxn.category.name if ltxn.category else None,
                            'category_group_slug': ltxn.category.group.slug if ltxn.category else None,
                            'classification_method': ltxn.classification_method,
                            'matched_rule_description': ltxn.matched_rule.description if ltxn.matched_rule else None,
                        }
                        raw_data['logical_transactions'].append(ltxn_data)

                    ledger_data['raw_transactions'].append(raw_data)

                stmt_data['ledgers'].append(ledger_data)

            stmt_list.append(stmt_data)

        acct_data['statement_imports'] = stmt_list
        accounts.append(acct_data)

    # Exchange rates referenced by the user's transactions
    txn_dates = (
        LogicalTransaction.objects.filter(user=user)
        .values_list('date', flat=True).distinct()
    )
    exchange_rates = []
    for er in ExchangeRate.objects.filter(date__in=txn_dates).order_by('date'):
        exchange_rates.append({
            'date': str(er.date),
            'usd_to_crc': _dec(er.usd_to_crc),
        })

    return {
        'version': EXPORT_VERSION,
        'exported_at': datetime.utcnow().isoformat() + 'Z',
        'user': {
            'email': user.email,
            'password': user.password,
            'is_active': user.is_active,
            'is_staff': user.is_staff,
        },
        'preferences': prefs,
        'categories': categories,
        'classification_rules': rules,
        'accounts': accounts,
        'exchange_rates': exchange_rates,
    }


# ── IMPORT ────────────────────────────────────────────────────────────

class ImportError(Exception):
    """Raised when import validation or restoration fails."""
    pass


def _check_user_is_fresh(user):
    """Raise ImportError if the user already has data beyond defaults."""
    non_default_cats = Category.objects.filter(user=user).exclude(name=Category.UNCLASSIFIED_NAME).count()
    if non_default_cats > 0:
        raise ImportError('Cannot import: user already has custom categories.')
    if ClassificationRule.objects.filter(user=user).exists():
        raise ImportError('Cannot import: user already has classification rules.')
    if Account.objects.filter(user=user).exists():
        raise ImportError('Cannot import: user already has accounts.')
    if StatementImport.objects.filter(user=user).exists():
        raise ImportError('Cannot import: user already has statement imports.')


def import_user_data(user, data):
    """
    Restore all data from an exported JSON dict into *user*'s account.

    The user must have no existing data (beyond auto-created Default categories).
    The entire operation is atomic — any error rolls back all changes.

    Returns a summary dict with counts of imported records.
    """
    version = data.get('version')
    if version != EXPORT_VERSION:
        raise ImportError(f'Unsupported export version: {version} (expected {EXPORT_VERSION})')

    _check_user_is_fresh(user)

    counts = {
        'categories': 0,
        'rules': 0,
        'accounts': 0,
        'statements': 0,
        'ledgers': 0,
        'raw_transactions': 0,
        'logical_transactions': 0,
        'exchange_rates': 0,
    }

    with transaction.atomic():
        # 1. Preferences
        prefs_data = data.get('preferences', {})
        if prefs_data:
            UserPreference.objects.update_or_create(
                user=user,
                defaults={'transaction_columns': prefs_data.get('transaction_columns', {})},
            )

        # 2. Categories (ensure groups exist first)
        cat_lookup = {}  # (group_slug, name) → Category
        # Pre-populate with existing defaults
        for cat in Category.objects.filter(user=user).select_related('group'):
            cat_lookup[(cat.group.slug, cat.name)] = cat

        for cat_data in data.get('categories', []):
            group = CategoryGroup.get_group(cat_data['group_slug'])
            key = (cat_data['group_slug'], cat_data['name'])
            if key in cat_lookup:
                # Update color of existing default categories
                existing = cat_lookup[key]
                if existing.color != cat_data.get('color', existing.color):
                    existing.color = cat_data['color']
                    existing.save(update_fields=['color'])
                continue
            cat = Category.objects.create(
                name=cat_data['name'],
                group=group,
                user=user,
                color=cat_data.get('color', '#6c757d'),
            )
            cat_lookup[key] = cat
            counts['categories'] += 1

        # 3. Classification rules
        rule_lookup = {}  # (group_slug, cat_name, description) → ClassificationRule
        for rule_data in data.get('classification_rules', []):
            cat_key = (rule_data['category_group_slug'], rule_data['category_name'])
            cat = cat_lookup.get(cat_key)
            if not cat:
                raise ImportError(
                    f"Rule references unknown category: {rule_data['category_name']} "
                    f"in group {rule_data['category_group_slug']}"
                )
            rule = ClassificationRule.objects.create(
                category=cat,
                user=user,
                description=rule_data.get('description', ''),
                account_type=rule_data.get('account_type', ''),
                amount_min=_to_decimal(rule_data.get('amount_min')),
                amount_max=_to_decimal(rule_data.get('amount_max')),
                metadata=rule_data.get('metadata', {}),
                detail=rule_data.get('detail', ''),
            )
            rule_lookup[(cat_key[0], cat_key[1], rule_data.get('description', ''))] = rule
            counts['rules'] += 1

        # 4. Accounts → Statements → Ledgers → RawTxns → LogicalTxns
        for acct_data in data.get('accounts', []):
            acct_type = acct_data['account_type']

            if acct_type == 'credit_account':
                credit_info = acct_data.get('credit_account', {})
                acct = CreditAccount.objects.create(
                    user=user,
                    account_type=acct_type,
                    card_holder=acct_data.get('card_holder', ''),
                    nickname=acct_data.get('nickname', ''),
                    card_number_hash=credit_info['card_number_hash'],
                    card_number_last4=credit_info.get('card_number_last4', ''),
                )
            elif acct_type == 'debit_account':
                debit_info = acct_data.get('debit_account', {})
                acct = DebitAccount.objects.create(
                    user=user,
                    account_type=acct_type,
                    card_holder=acct_data.get('card_holder', ''),
                    nickname=acct_data.get('nickname', ''),
                    iban=debit_info['iban'],
                    client_number=debit_info.get('client_number', ''),
                )
            else:
                raise ImportError(f"Unknown account type: {acct_type}")

            counts['accounts'] += 1

            for stmt_data in acct_data.get('statement_imports', []):
                stmt = StatementImport.objects.create(
                    account=acct,
                    user=user,
                    filename=stmt_data['filename'],
                    file_hash=stmt_data.get('file_hash', ''),
                    statement_date=_to_date(stmt_data['statement_date']) if stmt_data.get('statement_date') else None,
                    points_assigned=stmt_data.get('points_assigned', 0),
                    points_redeemable=stmt_data.get('points_redeemable', 0),
                )
                counts['statements'] += 1

                for ledger_data in stmt_data.get('ledgers', []):
                    ledger = CurrencyLedger.objects.create(
                        statement_import=stmt,
                        user=user,
                        currency=ledger_data['currency'],
                        previous_balance=_to_decimal(ledger_data.get('previous_balance', '0')),
                        balance_at_cutoff=_to_decimal(ledger_data.get('balance_at_cutoff', '0')),
                    )
                    counts['ledgers'] += 1

                    for raw_data in ledger_data.get('raw_transactions', []):
                        raw = RawTransaction.objects.create(
                            ledger=ledger,
                            user=user,
                            date=_to_date(raw_data['date']),
                            description=raw_data['description'],
                            amount=_to_decimal(raw_data['amount']),
                            account_metadata=raw_data.get('account_metadata', {}),
                        )
                        counts['raw_transactions'] += 1

                        for ltxn_data in raw_data.get('logical_transactions', []):
                            # Resolve category
                            ltxn_cat = None
                            if ltxn_data.get('category_name') and ltxn_data.get('category_group_slug'):
                                ltxn_cat = cat_lookup.get(
                                    (ltxn_data['category_group_slug'], ltxn_data['category_name'])
                                )
                            if ltxn_cat is None:
                                ltxn_cat = Category.get_unclassified(user)

                            # Resolve matched rule
                            matched_rule = None
                            if ltxn_data.get('matched_rule_description') is not None and ltxn_cat:
                                rule_key = (
                                    ltxn_cat.group.slug,
                                    ltxn_cat.name,
                                    ltxn_data['matched_rule_description'],
                                )
                                matched_rule = rule_lookup.get(rule_key)

                            LogicalTransaction.objects.create(
                                raw_transaction=raw,
                                user=user,
                                date=_to_date(ltxn_data.get('date', raw_data['date'])),
                                description=ltxn_data['description'],
                                amount=_to_decimal(ltxn_data['amount']),
                                amount_crc=_to_decimal(ltxn_data.get('amount_crc')),
                                amount_usd=_to_decimal(ltxn_data.get('amount_usd')),
                                category=ltxn_cat,
                                classification_method=ltxn_data.get('classification_method', 'unclassified'),
                                matched_rule=matched_rule,
                            )
                            counts['logical_transactions'] += 1

        # 5. Exchange rates
        for er_data in data.get('exchange_rates', []):
            _, created = ExchangeRate.objects.get_or_create(
                date=_to_date(er_data['date']),
                defaults={'usd_to_crc': _to_decimal(er_data['usd_to_crc'])},
            )
            if created:
                counts['exchange_rates'] += 1

    return counts
