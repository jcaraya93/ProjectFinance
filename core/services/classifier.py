"""
Transaction classifier — delegates to the YAML-based engine.
"""
from core.services.yaml_classifier import (
    classify_transaction_yaml,
    classify_transactions_yaml,
)


def classify_transaction(transaction):
    """Classify a single transaction. Returns (Category, matched_rule_or_None)."""
    return classify_transaction_yaml(transaction)


def classify_transactions(queryset=None):
    """Classify all unclassified transactions. Returns count of classified."""
    return classify_transactions_yaml(queryset)
