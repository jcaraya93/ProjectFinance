"""Rule matching and classification for ClassificationRuleV2.

Phases: transfer rules first, then specific categories, then the Unclassified fallbacks.
Within a phase the most specific rule wins (longest description, then most extra conditions).
"""
from decimal import Decimal

from core.models import ClassificationRuleV2, LogicalTransaction


def _match_rule(rule, description_upper, metadata, amount, account_type):
    """Return the number of matched conditions of a flat rule dict, or 0 if any condition fails."""
    matched = 0

    if 'description' in rule:
        if rule['description'].upper() in description_upper:
            matched += 1
        else:
            return 0

    if 'amount_min' in rule:
        if amount >= Decimal(str(rule['amount_min'])):
            matched += 1
        else:
            return 0

    if 'amount_max' in rule:
        if amount <= Decimal(str(rule['amount_max'])):
            matched += 1
        else:
            return 0

    if 'account_type' in rule:
        if rule['account_type'].lower() == account_type.lower():
            matched += 1
        else:
            return 0

    for key, value in rule.items():
        if key.startswith('metadata.'):
            meta_val = metadata.get(key[len('metadata.'):], '')
            if str(meta_val).upper() == str(value).upper():
                matched += 1
            else:
                return 0

    return matched


def _rule_phase(rule):
    """0 = transfer group (highest priority), 2 = Unclassified fallback, 1 = everything else."""
    if rule.category.group.slug == 'transfer':
        return 0
    if rule.category.name == 'Unclassified':
        return 2
    return 1


def _best_rule(flats, transaction):
    desc_upper = transaction.description.upper()
    metadata = transaction.account_metadata or {}
    try:
        account_type = transaction.ledger.statement_import.account.account_type
    except AttributeError:
        account_type = ''

    for phase in (0, 1, 2):
        best, best_specificity = None, 0
        for rule, flat in flats:
            if _rule_phase(rule) != phase:
                continue
            score = _match_rule(flat, desc_upper, metadata, transaction.amount, account_type)
            if score == 0:
                continue
            non_desc = score - (1 if 'description' in flat else 0)
            specificity = len(flat.get('description', '')) * 10 + non_desc
            if specificity > best_specificity:
                best, best_specificity = rule, specificity
        if best:
            return best
    return None


def _load_flats(user):
    rules = ClassificationRuleV2.objects.filter(user=user).select_related('category__group')
    return [(rule, rule.to_flat_dict()) for rule in rules]


def find_matching_rule(user, transaction):
    """Return the best ClassificationRuleV2 for a transaction, or None."""
    return _best_rule(_load_flats(user), transaction)


def classify_transactions_v2(user, dry_run=False, queryset=None, only_unclassified=False):
    """Assign V2 categories to a user's transactions using V2 rules.

    Writes only category_v2, matched_rule_v2 and classification_method_v2; V1 fields are never
    touched. Transactions whose V2 method is 'manual' are skipped, and transactions that match no
    rule are left as they are. Returns (total, changed, skipped_manual, unmatched).

    queryset limits the transactions considered (still restricted to the user);
    only_unclassified skips anything whose V2 method is not 'unclassified'.
    """
    flats = _load_flats(user)
    if queryset is None:
        queryset = LogicalTransaction.objects.all()
    queryset = queryset.filter(user=user).select_related(
        'raw_transaction__ledger__statement_import__account'
    )
    if only_unclassified:
        queryset = queryset.filter(classification_method_v2='unclassified')
    total = changed = skipped_manual = unmatched = 0
    to_update = []
    for txn in queryset.iterator(chunk_size=500):
        total += 1
        if txn.classification_method_v2 == 'manual':
            skipped_manual += 1
            continue
        rule = _best_rule(flats, txn)
        if rule is None:
            unmatched += 1
            continue
        if txn.category_v2_id == rule.category_id and txn.matched_rule_v2_id == rule.pk \
                and txn.classification_method_v2 == 'rule':
            continue
        txn.category_v2_id = rule.category_id
        txn.matched_rule_v2_id = rule.pk
        txn.classification_method_v2 = 'rule'
        to_update.append(txn)
        changed += 1
    if to_update and not dry_run:
        LogicalTransaction.objects.bulk_update(
            to_update, ['category_v2', 'matched_rule_v2', 'classification_method_v2'], batch_size=500
        )
    return total, changed, skipped_manual, unmatched
