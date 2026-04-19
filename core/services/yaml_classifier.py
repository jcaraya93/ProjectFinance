"""
YAML-based single-stage transaction classifier.

Rules are loaded from a hierarchical YAML config file (groups → categories → rules).
Each rule specifies conditions (description, metadata.<key>, amount_min, amount_max,
account_type). All conditions must match (AND). Most specific rule wins, with
ties broken by longest description keyword.
"""
import logging
import time
from decimal import Decimal
from pathlib import Path

import yaml
from django.conf import settings

from core.models import Category
from core.instrumentation import tracer, classification_result, classification_duration

logger = logging.getLogger(__name__)

DEFAULT_RULES_PATH = Path(settings.BASE_DIR) / 'classification_rules.yaml'


def get_rules_path():
    return getattr(settings, 'CLASSIFICATION_RULES_PATH', DEFAULT_RULES_PATH)


def load_yaml():
    """Load and return the raw YAML data dict."""
    path = get_rules_path()
    if not path.exists():
        logger.warning('Classification rules file not found: %s', path)
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def save_yaml(data):
    """Write YAML data back to disk and clear caches."""
    path = get_rules_path()
    header = (
        '# Classification Rules — hierarchical format\n'
        '# groups -> categories -> rules\n'
        '# Each rule specifies conditions (all must match). Most specific rule wins.\n\n'
    )
    body = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False, width=120)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(header + body)
    except OSError:
        logger.warning('Cannot write classification rules file: %s (permission denied or read-only)', path)
    reload_rules()


def _flatten_rules(data):
    """Flatten hierarchical YAML into a list of rule dicts with group/category injected."""
    flat = []
    groups = data.get('groups', {})
    for group_slug, group_info in groups.items():
        for cat_name, cat_info in group_info.get('categories', {}).items():
            for rule in cat_info.get('rules', []):
                flat.append({**rule, 'group': group_slug, 'category': cat_name})
    return flat


def _load_rules():
    """Load ClassificationRule objects from the database."""
    from core.models import ClassificationRule
    return list(ClassificationRule.objects.select_related('category__group').all())


def _load_rules_flat():
    """Load rules as flat dicts, falling back to YAML if DB is empty."""
    from core.models import ClassificationRule
    db_rules = ClassificationRule.objects.select_related('category__group').all()
    if db_rules.exists():
        return [r.to_flat_dict() for r in db_rules]
    return _flatten_rules(load_yaml())


def load_rules():
    """Load ClassificationRule objects, caching them. Call reload_rules() to refresh."""
    if not hasattr(load_rules, '_cache'):
        load_rules._cache = _load_rules()
    return load_rules._cache


def reload_rules():
    """Clear the cached rules so they are re-read from disk on next call."""
    if hasattr(load_rules, '_cache'):
        del load_rules._cache


def _match_rule(rule, description_upper, metadata, amount, account_type):
    """
    Check if a rule matches the transaction. Returns the number of
    matched conditions, or 0 if any condition fails.
    """
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

    # Match any 'metadata.<key>' conditions against account_metadata
    for key, value in rule.items():
        if key.startswith('metadata.'):
            meta_key = key[len('metadata.'):]
            meta_val = metadata.get(meta_key, '')
            if str(meta_val).upper() == str(value).upper():
                matched += 1
            else:
                return 0

    return matched


def _resolve_category(rule):
    """Resolve a rule's group+category strings to a Category object."""
    group_slug = rule.get('group', 'unclassified')
    category_name = rule.get('category')

    if not category_name:
        return None

    cat = Category.objects.filter(
        name=category_name,
        group__slug=group_slug,
    ).first()

    if not cat:
        cat = Category.objects.filter(name=category_name).first()

    if not cat:
        logger.warning(
            'Rule target not found: group=%s category=%s',
            group_slug, category_name,
        )
    return cat


def _rule_conditions(rule):
    """Extract just the condition keys from a flattened rule (exclude group/category/detail)."""
    skip = {'group', 'category', 'detail'}
    return {k: v for k, v in rule.items() if k not in skip}


def _rule_phase(rule_obj):
    """
    Return the classification phase for a rule:
      0 = transfer group (highest priority)
      1 = specific categories (not transfer, not Default)
      2 = fallback categories (expense/Default, income/Default)
    Accepts either a ClassificationRule object or a flat dict.
    """
    if hasattr(rule_obj, 'category'):
        group = rule_obj.category.group.slug
        category = rule_obj.category.name
    else:
        group = rule_obj.get('group', '')
        category = rule_obj.get('category', '')
    if group == 'transaction':
        return 0
    if category == 'Default':
        return 2
    return 1


def classify_transaction_yaml(transaction):
    """
    Classify a single transaction using classification rules with phase ordering.
    Phase 0: transfer rules, Phase 1: specific categories, Phase 2: Default.
    Returns (Category, ClassificationRule_or_None).
    """
    with tracer.start_as_current_span("classifier.classify_single") as span:
        span.set_attribute("transaction.id", transaction.id)
        span.set_attribute("transaction.description", transaction.description[:100])

        rule_objects = load_rules()
        desc_upper = transaction.description.upper()
        metadata = transaction.account_metadata or {}
        amount = transaction.amount

        try:
            account_type = transaction.ledger.statement_import.account.account_type
        except AttributeError:
            account_type = ''

        rules_evaluated = 0
        for phase in (0, 1, 2):
            best_rule_obj = None
            best_specificity = 0

            for rule_obj in rule_objects:
                if _rule_phase(rule_obj) != phase:
                    continue

                rules_evaluated += 1
                flat = rule_obj.to_flat_dict()
                score = _match_rule(flat, desc_upper, metadata, amount, account_type)
                if score == 0:
                    continue

                desc_len = len(flat.get('description', ''))
                non_desc_conditions = score - (1 if 'description' in flat else 0)
                specificity = (desc_len * 10) + non_desc_conditions

                if specificity > best_specificity:
                    best_rule_obj = rule_obj
                    best_specificity = specificity

            if best_rule_obj:
                span.set_attribute("classification.phase", phase)
                span.set_attribute("classification.rule_id", best_rule_obj.id)
                span.set_attribute("classification.category", best_rule_obj.category.name)
                span.set_attribute("classification.rules_evaluated", rules_evaluated)
                classification_result.add(1, {"method": "rule"})
                return best_rule_obj.category, best_rule_obj

        span.set_attribute("classification.phase", -1)
        span.set_attribute("classification.rules_evaluated", rules_evaluated)
        classification_result.add(1, {"method": "unclassified"})
        return Category.get_unclassified(transaction.user), None


def classify_transactions_yaml(queryset=None):
    """
    Classify logical transactions using rules. Skips manually classified.
    Returns count of classified transactions.
    """
    from core.models import LogicalTransaction

    with tracer.start_as_current_span("classifier.classify_batch") as span:
        if queryset is None:
            queryset = LogicalTransaction.objects.exclude(
                classification_method='manual'
            ).select_related(
                'category', 'raw_transaction__ledger__statement_import__account'
            )
        else:
            queryset = queryset.exclude(classification_method='manual')

        reload_rules()

        t0 = time.monotonic()
        total = 0
        count = 0
        for txn in queryset:
            total += 1
            category, rule_obj = classify_transaction_yaml(txn)
            if rule_obj and category != txn.category:
                txn.category = category
                txn.matched_rule = rule_obj
                txn.classification_method = 'rule'
                txn.save(update_fields=['category', 'matched_rule', 'classification_method'])
                count += 1

        elapsed_ms = (time.monotonic() - t0) * 1000
        span.set_attribute("classification.total", total)
        span.set_attribute("classification.classified_count", count)
        span.set_attribute("classification.unclassified_count", total - count)
        classification_duration.record(elapsed_ms, {"operation": "batch"})
        return count
