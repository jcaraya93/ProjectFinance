"""Tests for the classification engine — pure rule matching and DB-backed classification."""

from decimal import Decimal
from datetime import date

import pytest
from core.services.yaml_classifier import (
    _match_rule, _rule_phase,
    classify_transaction_yaml, classify_transactions_yaml, reload_rules,
)
from core.models import LogicalTransaction, ClassificationRule, Category
from core.tests.factories import (
    CreditAccountFactory, StatementImportFactory, CurrencyLedgerFactory,
    RawTransactionFactory, LogicalTransactionFactory,
)


# ── Pure rule matching (no DB) ──────────────────────────────────


class TestMatchRule:
    def test_description_match(self):
        rule = {'description': 'UBER'}
        assert _match_rule(rule, 'UBER TRIP CR', {}, Decimal('5000'), '') > 0

    def test_description_case_insensitive(self):
        rule = {'description': 'uber'}
        assert _match_rule(rule, 'UBER TRIP', {}, Decimal('5000'), '') > 0

    def test_description_no_match(self):
        rule = {'description': 'LYFT'}
        assert _match_rule(rule, 'UBER TRIP', {}, Decimal('5000'), '') == 0

    def test_description_substring(self):
        rule = {'description': 'STARBUCKS'}
        assert _match_rule(rule, 'STARBUCKS CITYZEN HEREDIA', {}, Decimal('3000'), '') > 0

    def test_amount_min_match(self):
        rule = {'amount_min': 100}
        assert _match_rule(rule, 'SOMETHING', {}, Decimal('150'), '') > 0

    def test_amount_min_reject(self):
        rule = {'amount_min': 100}
        assert _match_rule(rule, 'SOMETHING', {}, Decimal('50'), '') == 0

    def test_amount_min_boundary(self):
        rule = {'amount_min': 100}
        assert _match_rule(rule, 'SOMETHING', {}, Decimal('100'), '') > 0

    def test_amount_max_match(self):
        rule = {'amount_max': 200}
        assert _match_rule(rule, 'SOMETHING', {}, Decimal('150'), '') > 0

    def test_amount_max_reject(self):
        rule = {'amount_max': 200}
        assert _match_rule(rule, 'SOMETHING', {}, Decimal('250'), '') == 0

    def test_amount_max_boundary(self):
        rule = {'amount_max': 200}
        assert _match_rule(rule, 'SOMETHING', {}, Decimal('200'), '') > 0

    def test_amount_range(self):
        rule = {'amount_min': 100, 'amount_max': 200}
        assert _match_rule(rule, 'X', {}, Decimal('150'), '') > 0
        assert _match_rule(rule, 'X', {}, Decimal('50'), '') == 0
        assert _match_rule(rule, 'X', {}, Decimal('250'), '') == 0

    def test_account_type_match(self):
        rule = {'account_type': 'credit_account'}
        assert _match_rule(rule, 'X', {}, Decimal('100'), 'credit_account') > 0

    def test_account_type_reject(self):
        rule = {'account_type': 'credit_account'}
        assert _match_rule(rule, 'X', {}, Decimal('100'), 'debit_account') == 0

    def test_account_type_case_insensitive(self):
        rule = {'account_type': 'Credit_Account'}
        assert _match_rule(rule, 'X', {}, Decimal('100'), 'credit_account') > 0

    def test_metadata_match(self):
        rule = {'metadata.transaction_code': 'PT'}
        assert _match_rule(rule, 'X', {'transaction_code': 'PT'}, Decimal('100'), '') > 0

    def test_metadata_reject(self):
        rule = {'metadata.transaction_code': 'PT'}
        assert _match_rule(rule, 'X', {'transaction_code': 'TF'}, Decimal('100'), '') == 0

    def test_metadata_case_insensitive(self):
        rule = {'metadata.transaction_code': 'pt'}
        assert _match_rule(rule, 'X', {'transaction_code': 'PT'}, Decimal('100'), '') > 0

    def test_metadata_missing_key(self):
        rule = {'metadata.transaction_code': 'PT'}
        assert _match_rule(rule, 'X', {}, Decimal('100'), '') == 0

    def test_all_conditions_and(self):
        """All conditions must match (AND logic)."""
        rule = {'description': 'UBER', 'account_type': 'credit_account', 'amount_min': 1000}
        assert _match_rule(rule, 'UBER RIDES', {}, Decimal('5000'), 'credit_account') > 0
        assert _match_rule(rule, 'LYFT RIDES', {}, Decimal('5000'), 'credit_account') == 0
        assert _match_rule(rule, 'UBER RIDES', {}, Decimal('5000'), 'debit_account') == 0
        assert _match_rule(rule, 'UBER RIDES', {}, Decimal('500'), 'credit_account') == 0

    def test_specificity_more_conditions(self):
        """More matching conditions = higher score."""
        simple = {'description': 'UBER'}
        complex_rule = {'description': 'UBER', 'account_type': 'credit_account'}
        simple_score = _match_rule(simple, 'UBER TRIP', {}, Decimal('5000'), 'credit_account')
        complex_score = _match_rule(complex_rule, 'UBER TRIP', {}, Decimal('5000'), 'credit_account')
        assert complex_score > simple_score

    def test_empty_rule_matches_nothing(self):
        """A rule with no conditions returns 0."""
        rule = {}
        assert _match_rule(rule, 'ANYTHING', {}, Decimal('100'), 'credit_account') == 0

    def test_group_category_ignored_as_conditions(self):
        """group and category keys are not match conditions."""
        rule = {'description': 'UBER', 'group': 'expense', 'category': 'Transport'}
        assert _match_rule(rule, 'UBER TRIP', {}, Decimal('100'), '') > 0


class TestRulePhase:
    def test_transfer_phase_0(self):
        assert _rule_phase({'group': 'transaction', 'category': 'Transfer'}) == 0

    def test_specific_phase_1(self):
        assert _rule_phase({'group': 'expense', 'category': 'Groceries'}) == 1

    def test_default_phase_2(self):
        assert _rule_phase({'group': 'expense', 'category': 'Default'}) == 2

    def test_income_specific_phase_1(self):
        assert _rule_phase({'group': 'income', 'category': 'Salary'}) == 1

    def test_income_default_phase_2(self):
        assert _rule_phase({'group': 'income', 'category': 'Default'}) == 2


# ── DB-backed classification ────────────────────────────────────


@pytest.fixture(autouse=True)
def clear_rule_cache():
    reload_rules()
    yield
    reload_rules()


def _make_txn(user, description='TEST', amount=Decimal('5000.00'), txn_date=date(2025, 2, 1)):
    """Build full object chain: account → statement → ledger → raw → logical."""
    acct = CreditAccountFactory(user=user)
    stmt = StatementImportFactory(account=acct, user=user)
    ledger = CurrencyLedgerFactory(statement_import=stmt, user=user)
    raw = RawTransactionFactory(
        ledger=ledger, user=user, description=description,
        amount=amount, date=txn_date,
    )
    return LogicalTransactionFactory(
        raw_transaction=raw, user=user,
        description=description, amount=amount, date=txn_date,
    )


@pytest.mark.django_db
class TestClassifySingle:
    def test_match(self, user, expense_category):
        ClassificationRule.objects.create(
            category=expense_category, user=user, description='STARBUCKS',
        )
        reload_rules()

        txn = _make_txn(user, description='STARBUCKS CITYZEN')
        category, rule = classify_transaction_yaml(txn)

        assert category == expense_category
        assert rule is not None
        assert rule.description == 'STARBUCKS'

    def test_no_match(self, user, unclassified_category):
        txn = _make_txn(user, description='RANDOM STORE')
        category, rule = classify_transaction_yaml(txn)

        assert category.name == 'Default'
        assert rule is None

    def test_most_specific_wins(self, user, expense_category):
        cat_generic, _ = Category.objects.get_or_create(
            name='Transport', group=expense_category.group, user=user,
            defaults={'color': '#aabbcc'},
        )
        ClassificationRule.objects.create(
            category=cat_generic, user=user, description='UBER',
        )
        ClassificationRule.objects.create(
            category=expense_category, user=user, description='UBER EATS',
        )
        reload_rules()

        txn = _make_txn(user, description='UBER EATS DELIVERY')
        category, rule = classify_transaction_yaml(txn)

        assert category == expense_category
        assert rule.description == 'UBER EATS'

    def test_phase_ordering(self, user, expense_category, transfer_category):
        ClassificationRule.objects.create(
            category=expense_category, user=user, description='SINPE',
        )
        ClassificationRule.objects.create(
            category=transfer_category, user=user, description='SINPE',
        )
        reload_rules()

        txn = _make_txn(user, description='SINPE MOVIL Pago')
        category, rule = classify_transaction_yaml(txn)

        assert category == transfer_category


@pytest.mark.django_db
class TestClassifyBatch:
    def test_skips_manual(self, user, expense_category):
        ClassificationRule.objects.create(
            category=expense_category, user=user, description='CAFE',
        )
        reload_rules()

        txn = _make_txn(user, description='CAFE CENTRAL')
        txn.classification_method = 'manual'
        txn.save(update_fields=['classification_method'])

        count = classify_transactions_yaml(
            LogicalTransaction.objects.filter(pk=txn.pk)
        )
        assert count == 0

        txn.refresh_from_db()
        assert txn.classification_method == 'manual'

    def test_updates_db(self, user, expense_category):
        ClassificationRule.objects.create(
            category=expense_category, user=user, description='CAFE',
        )
        reload_rules()

        txns = [
            _make_txn(user, description='CAFE CENTRAL'),
            _make_txn(user, description='CAFE AROMA'),
            _make_txn(user, description='CAFE DOWNTOWN'),
        ]

        count = classify_transactions_yaml(
            LogicalTransaction.objects.filter(pk__in=[t.pk for t in txns])
        )
        assert count == 3

        for txn in txns:
            txn.refresh_from_db()
            assert txn.classification_method == 'rule'
            assert txn.category == expense_category


@pytest.mark.django_db
class TestRuleToFlatDict:
    def test_structure(self, user, expense_category):
        rule = ClassificationRule.objects.create(
            category=expense_category, user=user,
            description='STARBUCKS', account_type='credit_account',
            amount_min=Decimal('100'), amount_max=Decimal('50000'),
            metadata={'transaction_code': 'PT'}, detail='Coffee shops',
        )
        d = rule.to_flat_dict()

        assert d['group'] == 'expense'
        assert d['category'] == 'Groceries'
        assert d['description'] == 'STARBUCKS'
        assert d['account_type'] == 'credit_account'
        assert d['amount_min'] == 100.0
        assert d['amount_max'] == 50000.0
        assert d['metadata.transaction_code'] == 'PT'
        assert d['detail'] == 'Coffee shops'
