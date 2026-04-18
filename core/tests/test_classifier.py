import pytest
from decimal import Decimal

from core.services.yaml_classifier import _match_rule, _rule_phase


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
        rule = {'group': 'transaction', 'category': 'Transfer'}
        assert _rule_phase(rule) == 0

    def test_specific_phase_1(self):
        rule = {'group': 'expense', 'category': 'Groceries'}
        assert _rule_phase(rule) == 1

    def test_default_phase_2(self):
        rule = {'group': 'expense', 'category': 'Default'}
        assert _rule_phase(rule) == 2

    def test_income_specific_phase_1(self):
        rule = {'group': 'income', 'category': 'Salary'}
        assert _rule_phase(rule) == 1

    def test_income_default_phase_2(self):
        rule = {'group': 'income', 'category': 'Default'}
        assert _rule_phase(rule) == 2
