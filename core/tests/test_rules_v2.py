from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError

from core.models import Category, CategoryGroup, CategoryNode, ClassificationRule, ClassificationRuleV2, User
from core.services.rules_v2 import find_matching_rule, import_v1_rules


def node(user, name, slug='expense', parent=None):
    return CategoryNode.objects.create(
        name=name, user=user, group=CategoryGroup.get_group(slug), parent=parent,
    )


def txn(description, amount='10', metadata=None, account_type='debit_account'):
    ledger = SimpleNamespace(statement_import=SimpleNamespace(account=SimpleNamespace(account_type=account_type)))
    return SimpleNamespace(
        description=description, amount=Decimal(amount), account_metadata=metadata or {}, ledger=ledger,
    )


@pytest.mark.django_db
class TestClassificationRuleV2Model:
    def test_valid_rule_and_flat_dict(self, user):
        rule = ClassificationRuleV2.objects.create(
            category=node(user, 'Food'), user=user, description='SUPER', amount_max=Decimal('50'),
            metadata={'code': 'PT'},
        )
        assert rule.to_flat_dict() == {
            'group': 'expense', 'category': 'Food', 'description': 'SUPER',
            'amount_max': 50.0, 'metadata.code': 'PT',
        }

    def test_requires_a_condition(self, user):
        with pytest.raises(ValidationError):
            ClassificationRuleV2.objects.create(category=node(user, 'Food'), user=user)

    def test_rejects_inverted_amount_range(self, user):
        with pytest.raises(ValidationError):
            ClassificationRuleV2.objects.create(
                category=node(user, 'Food'), user=user, amount_min=Decimal('10'), amount_max=Decimal('5'),
            )

    def test_rejects_other_users_category(self, user):
        other = User.objects.create_user(email='o@example.com', password='x')
        with pytest.raises(ValidationError):
            ClassificationRuleV2.objects.create(category=node(other, 'Food'), user=user, description='x')

    def test_rules_follow_category_deletion(self, user):
        food = node(user, 'Food')
        ClassificationRuleV2.objects.create(category=food, user=user, description='x')
        food.delete()
        assert not ClassificationRuleV2.objects.exists()


@pytest.mark.django_db
class TestFindMatchingRule:
    def test_most_specific_description_wins(self, user):
        food = node(user, 'Food')
        groceries = node(user, 'Groceries', parent=food)
        ClassificationRuleV2.objects.create(category=food, user=user, description='SUPER')
        specific = ClassificationRuleV2.objects.create(category=groceries, user=user, description='SUPERMARKET')
        assert find_matching_rule(user, txn('Supermarket Central')) == specific

    def test_transfer_phase_beats_specific(self, user):
        food = node(user, 'Food')
        transfer = node(user, 'Own account', slug='transfer')
        ClassificationRuleV2.objects.create(category=food, user=user, description='SINPE MOVIL')
        t_rule = ClassificationRuleV2.objects.create(category=transfer, user=user, description='SINPE')
        assert find_matching_rule(user, txn('SINPE MOVIL')) == t_rule

    def test_unclassified_is_fallback_only(self, user):
        CategoryNode.ensure_protected(user)
        fallback = CategoryNode.objects.get(user=user, name='Unclassified', group__slug='expense')
        ClassificationRuleV2.objects.create(category=fallback, user=user, description='COFFEE SHOP')
        food = node(user, 'Food')
        specific = ClassificationRuleV2.objects.create(category=food, user=user, description='COFFEE')
        assert find_matching_rule(user, txn('Coffee Shop 12')) == specific

    def test_no_match_and_other_users_rules_ignored(self, user):
        other = User.objects.create_user(email='o@example.com', password='x')
        ClassificationRuleV2.objects.create(category=node(other, 'Food'), user=other, description='SUPER')
        assert find_matching_rule(user, txn('Supermarket')) is None

    def test_account_type_and_metadata_conditions(self, user):
        food = node(user, 'Food')
        rule = ClassificationRuleV2.objects.create(
            category=food, user=user, account_type='credit_account', metadata={'code': 'PT'},
        )
        assert find_matching_rule(user, txn('x', account_type='credit_account', metadata={'code': 'pt'})) == rule
        assert find_matching_rule(user, txn('x', account_type='debit_account', metadata={'code': 'PT'})) is None


@pytest.mark.django_db
class TestImportV1Rules:
    def test_imports_by_group_and_name_and_is_idempotent(self, user, category_groups):
        expense = CategoryGroup.get_group(CategoryGroup.EXPENSE)
        v1_food = Category.objects.create(name='Food', group=expense, user=user)
        v1_gone = Category.objects.create(name='Gone', group=expense, user=user)
        ClassificationRule.objects.create(
            category=v1_food, user=user, description='SUPER', amount_min=Decimal('5'), metadata={'k': 'v'},
        )
        ClassificationRule.objects.create(category=v1_gone, user=user, description='GONE')
        node(user, 'Food')

        assert import_v1_rules(user) == (1, 0, 1)
        assert import_v1_rules(user) == (0, 1, 1)

        rule = ClassificationRuleV2.objects.get(user=user)
        assert rule.category.name == 'Food' and rule.description == 'SUPER'
        assert rule.amount_min == Decimal('5') and rule.metadata == {'k': 'v'}
        assert ClassificationRule.objects.filter(user=user).count() == 2

    def test_unclassified_rules_map_to_protected_node(self, user, category_groups):
        expense = CategoryGroup.get_group(CategoryGroup.EXPENSE)
        v1 = Category.objects.get(name='Unclassified', group=expense, user=user)
        ClassificationRule.objects.create(category=v1, user=user, description='MISC')
        assert import_v1_rules(user) == (1, 0, 0)
        assert ClassificationRuleV2.objects.get(user=user).category.is_protected
