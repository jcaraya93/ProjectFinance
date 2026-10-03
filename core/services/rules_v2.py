"""Rule matching and V1 import for ClassificationRuleV2.

Reuses the V1 matcher and phase ordering (transfer, specific, Unclassified) so both
rule sets behave identically. Nothing here writes to transactions.
"""
from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction

from core.models import Category, CategoryNode, ClassificationRule, ClassificationRuleV2
from core.services.yaml_classifier import _match_rule, _rule_phase


def find_matching_rule(user, transaction):
    """Return the best ClassificationRuleV2 for a transaction, or None."""
    rules = list(
        ClassificationRuleV2.objects.filter(user=user).select_related('category__group')
    )
    desc_upper = transaction.description.upper()
    metadata = transaction.account_metadata or {}
    try:
        account_type = transaction.ledger.statement_import.account.account_type
    except AttributeError:
        account_type = ''

    flats = [(rule, rule.to_flat_dict()) for rule in rules]
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


def import_v1_rules(user):
    """Copy V1 rules to V2, targeting the CategoryNode with the same group and name.

    Idempotent. Returns (created, skipped_existing, skipped_unusable): the last counts rules with
    no matching V2 category or no usable conditions.
    """
    CategoryNode.ensure_protected(user)
    nodes = {(n.group_id, n.name): n for n in CategoryNode.objects.filter(user=user)}
    existing = {
        (r.category_id, r.description, r.account_type, r.amount_min, r.amount_max,
         tuple(sorted(r.metadata.items())))
        for r in ClassificationRuleV2.objects.filter(user=user)
    }
    created = skipped_existing = skipped_missing = 0
    with db_transaction.atomic():
        for rule in ClassificationRule.objects.filter(user=user).select_related('category'):
            node = nodes.get((rule.category.group_id, rule.category.name))
            if node is None:
                skipped_missing += 1
                continue
            key = (node.pk, rule.description, rule.account_type, rule.amount_min, rule.amount_max,
                   tuple(sorted(rule.metadata.items())))
            if key in existing:
                skipped_existing += 1
                continue
            try:
                ClassificationRuleV2.objects.create(
                    category=node, user=user, description=rule.description, account_type=rule.account_type,
                    amount_min=rule.amount_min, amount_max=rule.amount_max, metadata=rule.metadata,
                    detail=rule.detail,
                )
            except ValidationError:
                # e.g. a V1 rule with no conditions can never match
                skipped_missing += 1
                continue
            existing.add(key)
            created += 1
    return created, skipped_existing, skipped_missing
