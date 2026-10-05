import pytest
from django.urls import reverse

from core.models import LogicalTransaction, Tag, TagGroup, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def txns(user):
    from core.tests.factories import LogicalTransactionFactory, RawTransactionFactory
    out = []
    for i in range(3):
        raw = RawTransactionFactory(user=user, description=f'T{i}', ledger__user=user, ledger__statement_import__user=user,
                                    ledger__statement_import__account__user=user)
        out.append(LogicalTransactionFactory(user=user, raw_transaction=raw, description=f'T{i}'))
    return out


class TestTagGroupsAndDates:
    def test_tags_are_ordered_by_start_date_most_recent_first(self, auth_client, user):
        group = TagGroup.objects.create(user=user, name='Trips')
        Tag.objects.create(user=user, group=group, name='Later', start_date='2025-11-03', end_date='2025-11-04')
        Tag.objects.create(user=user, group=group, name='Undated')
        Tag.objects.create(user=user, group=group, name='Earlier', start_date='2025-10-27', end_date='2025-10-28')

        response = auth_client.get(reverse('core:tag_list'))

        section = next(section for section in response.context['sections'] if section['group'] == group)
        assert [tag.name for tag in section['tags']] == ['Later', 'Earlier', 'Undated']

    def test_group_crud_and_ungroup_on_delete(self, auth_client, user):
        auth_client.post(reverse('core:tag_group_save'), {'name': 'Trips'})
        group = TagGroup.objects.get(user=user)
        auth_client.post(reverse('core:tag_group_save'), {'name': 'trips'})
        assert TagGroup.objects.filter(user=user).count() == 1
        auth_client.post(reverse('core:tag_save'), {'name': 'Mexico', 'group': group.pk,
                                                    'start_date': '2025-10-27', 'end_date': '2025-11-03'})
        tag = Tag.objects.get(user=user)
        assert tag.group == group and tag.duration_days == 8
        html = auth_client.get(reverse('core:tag_list')).content.decode()
        assert 'Mexico' in html and '2025-10-27' in html
        auth_client.post(reverse('core:tag_group_delete'), {'id': group.pk})
        tag.refresh_from_db()
        assert tag.group is None

    def test_date_validation(self, auth_client, user):
        post = lambda **kw: auth_client.post(reverse('core:tag_save'), {'name': 'X', **kw})
        post(start_date='2025-10-27')
        post(start_date='2025-11-03', end_date='2025-10-27')
        assert not Tag.objects.exists()

    def test_other_users_group_rejected(self, auth_client, user):
        other = User.objects.create_user(email='o@example.com', password='x')
        theirs = TagGroup.objects.create(user=other, name='Theirs')
        auth_client.post(reverse('core:tag_save'), {'name': 'X', 'group': theirs.pk})
        assert not Tag.objects.exists()

    def test_dated_tags_appear_in_transactions_date_presets(self, auth_client, user):
        Tag.objects.create(user=user, name='NYC', start_date='2026-05-01', end_date='2026-05-06')
        html = auth_client.get(reverse('core:transaction_list')).content.decode()
        assert 'data-start="2026-05-01"' in html and 'NYC' in html


class TestTagManagement:
    def test_create_edit_delete(self, auth_client, user):
        auth_client.post(reverse('core:tag_save'), {'name': 'Mexico', 'color': '#ff0000'})
        tag = Tag.objects.get(user=user)
        assert (tag.name, tag.color) == ('Mexico', '#ff0000')
        assert 'Mexico' in auth_client.get(reverse('core:tag_list')).content.decode()
        auth_client.post(reverse('core:tag_save'), {'id': tag.pk, 'name': 'Mexico 2025', 'color': '#00ff00'})
        tag.refresh_from_db()
        assert (tag.name, tag.color) == ('Mexico 2025', '#00ff00')
        auth_client.post(reverse('core:tag_delete'), {'id': tag.pk})
        assert not Tag.objects.exists()

    def test_rejects_duplicates_and_bad_input(self, auth_client, user):
        url = reverse('core:tag_save')
        auth_client.post(url, {'name': 'Trip'})
        auth_client.post(url, {'name': 'trip'})
        auth_client.post(url, {'name': ''})
        auth_client.post(url, {'name': 'Bad', 'color': 'red'})
        auth_client.post(url, {'name': 'a,b'})
        assert list(Tag.objects.values_list('name', flat=True)) == ['Trip']

    def test_cannot_touch_other_users_tags(self, auth_client):
        other = User.objects.create_user(email='other@example.com', password='x')
        theirs = Tag.objects.create(user=other, name='Theirs')
        assert auth_client.post(reverse('core:tag_save'), {'id': theirs.pk, 'name': 'x'}).status_code == 404
        assert auth_client.post(reverse('core:tag_delete'), {'id': theirs.pk}).status_code == 404

    def test_deleting_tag_keeps_transactions(self, auth_client, user, txns):
        tag = Tag.objects.create(user=user, name='X')
        txns[0].tags.add(tag)
        auth_client.post(reverse('core:tag_delete'), {'id': tag.pk})
        assert LogicalTransaction.objects.filter(pk=txns[0].pk).exists()


class TestTagRuleCount:
    def test_rule_count_and_filtered_rules_page(self, auth_client, user, category_groups):
        from core.models import CategoryGroup, CategoryNode, ClassificationRuleV2

        node = CategoryNode.objects.create(name='Food', user=user, group=CategoryGroup.get_group('expense'))
        used, unused = Tag.objects.create(user=user, name='Used'), Tag.objects.create(user=user, name='Unused')
        for desc in ('A', 'B'):
            ClassificationRuleV2.objects.create(category=node, user=user, description=desc).tags.add(used)
        ClassificationRuleV2.objects.create(category=node, user=user, description='C')
        ctx = auth_client.get(reverse('core:tag_list')).context
        tags = {t.name: t for sec in ctx['sections'] for t in sec['tags']}
        tags.update({t.name: t for t in ctx.get('ungrouped', [])})
        assert (tags['Used'].rule_count, tags['Unused'].rule_count) == (2, 0)
        resp = auth_client.get(reverse('core:rules_v2_list'), {'tag': used.pk})
        assert sorted(r.description for r in resp.context['rules']) == ['A', 'B']


class TestBulkTag:
    def test_add_and_remove(self, auth_client, user, txns):
        tag = Tag.objects.create(user=user, name='Trip')
        ids = [t.pk for t in txns[:2]]
        auth_client.post(reverse('core:bulk_tag'), {'txn_ids': ids, 'tag_id': tag.pk, 'mode': 'add'})
        assert set(tag.logical_transactions.values_list('pk', flat=True)) == set(ids)
        auth_client.post(reverse('core:bulk_tag'), {'txn_ids': ids, 'tag_id': tag.pk, 'mode': 'add'})
        assert tag.logical_transactions.count() == 2
        auth_client.post(reverse('core:bulk_tag'), {'txn_ids': [ids[0]], 'tag_id': tag.pk, 'mode': 'remove'})
        assert list(tag.logical_transactions.values_list('pk', flat=True)) == [ids[1]]

    def test_new_tag_name_is_ignored(self, auth_client, user, txns):
        auth_client.post(reverse('core:bulk_tag'), {'txn_ids': [txns[0].pk], 'new_tag': 'Mexico', 'mode': 'add'})
        assert not Tag.objects.filter(user=user).exists()

    def test_select_all_matching_uses_filters(self, auth_client, user, txns):
        tag = Tag.objects.create(user=user, name='T')
        auth_client.post(reverse('core:bulk_tag'), {
            'select_all_matching': '1', 'filter_qs': 'search=T1', 'tag_id': tag.pk, 'mode': 'add'})
        assert list(tag.logical_transactions.values_list('description', flat=True)) == ['T1']

    def test_rejects_other_users_data(self, auth_client, user, txns):
        other = User.objects.create_user(email='other@example.com', password='x')
        theirs = Tag.objects.create(user=other, name='Theirs')
        assert auth_client.post(reverse('core:bulk_tag'), {
            'txn_ids': [txns[0].pk], 'tag_id': theirs.pk, 'mode': 'add'}).status_code == 404
        mine = Tag.objects.create(user=user, name='Mine')
        auth_client.post(reverse('core:bulk_tag'), {'txn_ids': [txns[0].pk], 'mode': 'bogus', 'tag_id': mine.pk})
        assert not mine.logical_transactions.exists()


class TestTagFilterAndEdit:
    def test_filter_by_tag_and_chips(self, auth_client, user, txns):
        a, b = Tag.objects.create(user=user, name='A'), Tag.objects.create(user=user, name='B')
        txns[0].tags.add(a)
        txns[1].tags.add(a, b)
        resp = auth_client.get(reverse('core:transaction_list'), {'tag': [a.pk]})
        assert {t.description for t in resp.context['page_obj']} == {'T0', 'T1'}
        assert resp.content.decode().count('title="Show transactions tagged A"') == 2
        resp = auth_client.get(reverse('core:transaction_list'), {'tag': [b.pk]})
        assert [t.description for t in resp.context['page_obj']] == ['T1']

    def test_edit_transaction_sets_tags(self, auth_client, user, txns):
        txn = txns[0]
        mexico, food = Tag.objects.create(user=user, name='Mexico'), Tag.objects.create(user=user, name='food')
        url = reverse('core:edit_transaction', args=[txn.raw_transaction_id])
        row = {'action': 'save', 'split_id': txn.pk, 'split_description': txn.description, 'split_note': '',
               'split_category': txn.category_v2_id or _any_category(user), 'split_amount': txn.amount}
        auth_client.post(url, {**row, 'split_tags': f'{mexico.pk},{food.pk}'})
        assert sorted(txn.tags.values_list('name', flat=True)) == ['Mexico', 'food']
        auth_client.post(url, {**row, 'split_tags': ''})
        assert not txn.tags.exists()
        assert 'tag-check' in auth_client.get(url).content.decode()

    def test_edit_transaction_rejects_unknown_or_foreign_tags(self, auth_client, user, txns):
        txn = txns[0]
        theirs = Tag.objects.create(user=User.objects.create_user(email='z@example.com', password='x'), name='Theirs')
        auth_client.post(reverse('core:edit_transaction', args=[txn.raw_transaction_id]), {
            'action': 'save', 'split_id': txn.pk, 'split_description': txn.description, 'split_note': '',
            'split_category': txn.category_v2_id or _any_category(user), 'split_amount': txn.amount,
            'split_tags': str(theirs.pk), 'split_new': 'Free'})
        assert not txn.tags.exists() and not Tag.objects.filter(name='Free').exists()

    def test_edit_without_tags_field_leaves_tags(self, auth_client, user, txns):
        txn = txns[0]
        tag = Tag.objects.create(user=user, name='Keep')
        txn.tags.add(tag)
        auth_client.post(reverse('core:edit_transaction', args=[txn.raw_transaction_id]), {
            'action': 'save', 'split_id': txn.pk, 'split_description': txn.description, 'split_note': '',
            'split_category': txn.category_v2_id or _any_category(user), 'split_amount': txn.amount})
        assert list(txn.tags.all()) == [tag]


def _any_category(user):
    from core.models import CategoryGroup, CategoryNode
    return CategoryNode.objects.create(name='Misc', user=user, group=CategoryGroup.get_group('expense')).pk