import pytest
from django.core.exceptions import ValidationError

from core.models import CategoryGroup, CategoryNode, ClassificationRuleV2, User


@pytest.mark.django_db
class TestTransactionV2Category:
    def test_defaults_to_unassigned(self, sample_data):
        txn = sample_data['transactions'][0]
        assert txn.category_v2 is None
        assert txn.matched_rule_v2 is None
        assert txn.classification_method_v2 == 'unclassified'

    def test_can_assign_any_level_without_touching_v1(self, user, sample_data):
        txn = sample_data['transactions'][0]
        group = CategoryGroup.get_group('expense')
        parent = CategoryNode.objects.create(name='Food', user=user, group=group)
        child = CategoryNode.objects.create(name='Snacks', user=user, group=group, parent=parent)
        v1_category = txn.category
        for node in (parent, child):
            txn.category_v2 = node
            txn.full_clean()
            txn.save()
        txn.refresh_from_db()
        assert txn.category_v2 == child
        assert txn.category == v1_category
        assert list(child.logical_transactions.all()) == [txn]

    def test_other_users_node_or_rule_is_rejected(self, user, sample_data):
        txn = sample_data['transactions'][0]
        other = User.objects.create_user(email='other@example.com', password='x')
        node = CategoryNode.objects.create(name='Food', user=other, group=CategoryGroup.get_group('expense'))
        txn.category_v2 = node
        with pytest.raises(ValidationError):
            txn.full_clean()
        txn.category_v2 = None
        txn.matched_rule_v2 = ClassificationRuleV2.objects.create(category=node, user=other, description='X')
        with pytest.raises(ValidationError):
            txn.full_clean()

    def test_deleting_node_or_rule_unassigns(self, user, sample_data):
        txn = sample_data['transactions'][0]
        node = CategoryNode.objects.create(name='Food', user=user, group=CategoryGroup.get_group('expense'))
        rule = ClassificationRuleV2.objects.create(category=node, user=user, description='X')
        txn.category_v2, txn.matched_rule_v2, txn.classification_method_v2 = node, rule, 'rule'
        txn.save()
        rule.delete()
        node.delete()
        txn.refresh_from_db()
        assert txn.category_v2 is None and txn.matched_rule_v2 is None
