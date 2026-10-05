import pytest

from core.models import CategoryGroup, CategoryNode, ClassificationRuleV2, LogicalTransaction, Tag
from core.services.rules_v2 import classify_transactions_v2


@pytest.mark.django_db
class TestClassifyTransactionsV2:
    def _setup(self, user, sample_data):
        sample_data['rule'].delete()
        LogicalTransaction.objects.filter(user=user).update(
            category_v2=None, matched_rule_v2=None, classification_method_v2='unclassified',
        )
        node = CategoryNode.objects.create(name='Food', user=user, group=CategoryGroup.get_group('expense'))
        rule = ClassificationRuleV2.objects.create(category=node, user=user, description='TRANSACTION 1')
        return node, rule

    def test_assigns_matching_transaction_only(self, user, sample_data):
        node, rule = self._setup(user, sample_data)
        txns = sample_data['transactions']
        total, changed, manual, unmatched = classify_transactions_v2(user)
        assert (total, changed, manual, unmatched) == (5, 1, 0, 4)
        for t in txns:
            t.refresh_from_db()
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
        assert hit.category_v2 != node
        hit.classification_method_v2 = 'unclassified'
        hit.save()
        assert classify_transactions_v2(user)[1] == 1
        assert classify_transactions_v2(user)[1] == 0

    def test_dry_run_saves_nothing(self, user, sample_data):
        self._setup(user, sample_data)
        assert classify_transactions_v2(user, dry_run=True)[1] == 1
        for t in sample_data['transactions']:
            t.refresh_from_db()
            assert t.category_v2_id is None

    @pytest.mark.parametrize('existing_note,rule_note,expected', [
        ('', 'Rule note', 'Rule note'),
        ('My note', 'Rule note', 'My note'),
        ('', '', ''),
    ])
    def test_rule_note_fills_empty_notes_only(self, user, sample_data, existing_note, rule_note, expected):
        _, rule = self._setup(user, sample_data)
        rule.detail = rule_note
        rule.save()
        hit = sample_data['transactions'][1]
        hit.refresh_from_db()
        hit.note = existing_note
        hit.save()
        assert classify_transactions_v2(user, only_unclassified=True)[1] == 1
        hit.refresh_from_db()
        assert hit.note == expected
        assert classify_transactions_v2(user)[1] == 0
        for other in sample_data['transactions']:
            if other.pk != hit.pk:
                other.refresh_from_db()
                assert other.note == ''

    def test_rule_note_updates_already_matched_transaction(self, user, sample_data):
        _, rule = self._setup(user, sample_data)
        classify_transactions_v2(user)
        rule.detail = 'New rule note'
        rule.save()
        hit = sample_data['transactions'][1]
        assert classify_transactions_v2(user, dry_run=True)[1] == 1
        hit.refresh_from_db()
        assert hit.note == ''
        assert classify_transactions_v2(user)[1] == 1
        hit.refresh_from_db()
        assert hit.note == 'New rule note'
        assert classify_transactions_v2(user)[1] == 0

    def test_rule_tags_are_added_and_existing_tags_kept(self, user, sample_data):
        _, rule = self._setup(user, sample_data)
        a, b = Tag.objects.create(user=user, name='A'), Tag.objects.create(user=user, name='B')
        rule.tags.set([a])
        hit = sample_data['transactions'][1]
        hit.tags.add(b)
        assert classify_transactions_v2(user, dry_run=True)[1] == 1
        assert set(hit.tags.all()) == {b}
        assert classify_transactions_v2(user)[1] == 1
        assert set(hit.tags.all()) == {a, b}
        assert classify_transactions_v2(user)[1] == 0
        others = [t for t in sample_data['transactions'] if t.pk != hit.pk]
        assert not any(t.tags.exists() for t in others)

    def test_rule_tags_added_to_already_matched_transaction_and_skip_manual(self, user, sample_data):
        _, rule = self._setup(user, sample_data)
        classify_transactions_v2(user)
        rule.tags.add(Tag.objects.create(user=user, name='A'))
        hit = sample_data['transactions'][1]
        assert classify_transactions_v2(user)[1] == 1
        assert hit.tags.count() == 1
        hit.tags.clear()
        hit.classification_method_v2 = 'manual'
        hit.save()
        classify_transactions_v2(user)
        assert hit.tags.count() == 0

    def test_rule_note_leaves_manual_transactions_untouched(self, user, sample_data):
        _, rule = self._setup(user, sample_data)
        rule.detail = 'Rule note'
        rule.save()
        hit = sample_data['transactions'][1]
        hit.classification_method_v2 = 'manual'
        hit.save()
        assert classify_transactions_v2(user)[1:3] == (0, 1)
        hit.refresh_from_db()
        assert hit.note == ''
