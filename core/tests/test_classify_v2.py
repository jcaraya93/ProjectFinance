import pytest

from core.models import CategoryGroup, CategoryNode, ClassificationRuleV2
from core.services.rules_v2 import classify_transactions_v2, sync_manual_to_v2


@pytest.mark.django_db
class TestClassifyTransactionsV2:
    def _setup(self, user, sample_data):
        node = CategoryNode.objects.create(name='Food', user=user, group=CategoryGroup.get_group('expense'))
        rule = ClassificationRuleV2.objects.create(category=node, user=user, description='TRANSACTION 1')
        return node, rule

    def snapshot_v1(self, txns):
        return [(t.pk, t.category_id, t.matched_rule_id, t.classification_method) for t in txns]

    def test_assigns_v2_only_and_leaves_v1_untouched(self, user, sample_data):
        node, rule = self._setup(user, sample_data)
        txns = sample_data['transactions']
        before = self.snapshot_v1(txns)
        total, changed, manual, unmatched = classify_transactions_v2(user)
        assert (total, changed, manual, unmatched) == (5, 1, 0, 4)
        for t in txns:
            t.refresh_from_db()
        assert self.snapshot_v1(txns) == before
        hit = txns[1]
        assert (hit.category_v2, hit.matched_rule_v2, hit.classification_method_v2) == (node, rule, 'rule')
        assert all(t.category_v2 is None for t in txns if t.pk != hit.pk)

    def test_manual_v2_is_skipped_and_rerun_is_idempotent(self, user, sample_data):
        node, rule = self._setup(user, sample_data)
        hit = sample_data['transactions'][1]
        hit.classification_method_v2 = 'manual'
        hit.save()
        assert classify_transactions_v2(user)[1:3] == (0, 1)
        hit.refresh_from_db()
        assert hit.category_v2 is None
        hit.classification_method_v2 = 'unclassified'
        hit.save()
        assert classify_transactions_v2(user)[1] == 1
        assert classify_transactions_v2(user)[1] == 0

    def test_dry_run_saves_nothing(self, user, sample_data):
        self._setup(user, sample_data)
        assert classify_transactions_v2(user, dry_run=True)[1] == 1
        assert not any(t.category_v2_id for t in sample_data['transactions'])
        for t in sample_data['transactions']:
            t.refresh_from_db()
            assert t.category_v2_id is None


@pytest.mark.django_db
class TestSyncManualToV2:
    def test_copies_manual_and_protects_from_rules(self, user, sample_data):
        group = CategoryGroup.get_group('expense')
        txns = sample_data['transactions']
        v1 = txns[0].category
        node = CategoryNode.objects.create(name=v1.name, user=user, group=v1.group)
        txns[0].classification_method = 'manual'
        txns[0].save()
        before = [(t.pk, t.category_id, t.matched_rule_id, t.classification_method) for t in txns]
        ClassificationRuleV2.objects.create(
            category=CategoryNode.objects.create(name='Other', user=user, group=group),
            user=user, description='TRANSACTION')
        assert sync_manual_to_v2(user, dry_run=True) == (1, 1, 0)
        txns[0].refresh_from_db()
        assert txns[0].category_v2 is None
        assert sync_manual_to_v2(user) == (1, 1, 0)
        assert sync_manual_to_v2(user) == (1, 0, 0)
        classify_transactions_v2(user)
        for t in txns:
            t.refresh_from_db()
        assert (txns[0].category_v2, txns[0].classification_method_v2) == (node, 'manual')
        assert all(t.category_v2.name == 'Other' for t in txns[1:])
        assert [(t.pk, t.category_id, t.matched_rule_id, t.classification_method) for t in txns] == before

    def test_unmapped_manual_is_left_alone(self, user, sample_data):
        t = sample_data['transactions'][0]
        t.classification_method = 'manual'
        t.save()
        assert sync_manual_to_v2(user) == (1, 0, 1)
        t.refresh_from_db()
        assert t.category_v2 is None
