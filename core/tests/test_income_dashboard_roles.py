from datetime import date
from html import unescape
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from django.urls import reverse

from core.models import CategoryGroup, CategoryNode
from core.services.income_categories import income_category_roles
from core.tests.factories import LogicalTransactionFactory, UserFactory


@pytest.mark.parametrize('role,route,total_key,ids_key', [
    ('salary', 'income_salary_dashboard', 'salary_total', 'salary_category_ids'),
    ('bonus', 'income_bonus_dashboard', 'bonus_total', 'extras_category_ids'),
    ('association', 'income_bonus_dashboard', 'association_total', 'extras_category_ids'),
    ('government', 'income_bonus_dashboard', 'goverment_total', 'extras_category_ids'),
    ('reimbursement', 'reimbursement_overview_dashboard', 'all_time_total', 'reimbursement_category_ids'),
    ('bank', 'bank_income_overview_dashboard', 'all_time_total', 'bank_category_ids'),
])
def test_income_dashboard_survives_rename(auth_client, user, role, route, total_key, ids_key):
    group = CategoryGroup.get_group('income')
    parent = CategoryNode.objects.create(user=user, group=group, name='Original', income_dashboard_role=role)
    child = CategoryNode.objects.create(user=user, group=group, name='Child', parent=parent)
    target = LogicalTransactionFactory(user=user, category_v2=child, date=date(2025, 2, 1),
                                       amount=100, amount_crc=100, amount_usd=2)
    other = UserFactory()
    other_node = CategoryNode.objects.create(user=other, group=group, name='Other', income_dashboard_role=role)
    LogicalTransactionFactory(user=other, category_v2=other_node, amount_crc=99999)
    url = reverse('core:' + route)
    before = auth_client.get(url, {'period_type': 'all'})
    parent.name = 'Completely Renamed'
    parent.save()
    child.name = 'Also Renamed'
    child.save()
    after = auth_client.get(url, {'period_type': 'all'})
    assert before.context[total_key] == after.context[total_key] == 100
    assert before.context[ids_key] == after.context[ids_key]
    usd = auth_client.get(url, {'display_currency': 'USD', 'period_type': 'all'})
    assert usd.context[total_key] == 2
    links = re.findall(r'href="([^"]+)"', after.content.decode())
    tx_link = next(unescape(link) for link in links if link.startswith('/transactions/?group=income'))
    params = parse_qs(urlsplit(tx_link).query)
    assert set(params['category']) == {str(parent.pk), str(child.pk)}
    assert params['category_scope'] == ['direct']
    transactions = auth_client.get(tx_link)
    assert {tx.pk for tx in transactions.context['page_obj']} == {target.pk}


def test_nearest_role_overrides_parent_and_is_user_scoped(user):
    group = CategoryGroup.get_group('income')
    parent = CategoryNode.objects.create(user=user, group=group, name='Parent', income_dashboard_role='salary')
    child = CategoryNode.objects.create(user=user, group=group, name='Child', parent=parent, income_dashboard_role='bonus')
    leaf = CategoryNode.objects.create(user=user, group=group, name='Leaf', parent=child)
    roles = income_category_roles(user)
    assert roles['salary'] == {parent.pk}
    assert roles['bonus'] == {child.pk, leaf.pk}


def test_role_can_be_assigned_in_category_editor_and_survives_rename(auth_client, user):
    node = CategoryNode.objects.create(user=user, group=CategoryGroup.get_group('income'), name='Pay')
    url = reverse('core:category_v2_save')
    response = auth_client.post(url, {'id': node.pk, 'name': 'Pay', 'income_dashboard_role': 'salary'})
    assert response.status_code == 302
    node.refresh_from_db()
    assert node.income_dashboard_role == 'salary'
    auth_client.post(url, {'id': node.pk, 'name': 'New Pay'})
    node.refresh_from_db()
    assert node.income_dashboard_role == 'salary'
    invalid = auth_client.post(url, {'id': node.pk, 'name': 'New Pay', 'income_dashboard_role': 'invalid'}, follow=True)
    from django.contrib.messages import get_messages
    assert any('not a valid choice' in str(message) for message in get_messages(invalid.wsgi_request))
    node.refresh_from_db()
    assert node.income_dashboard_role == 'salary'


def test_missing_assignment_warns_and_has_no_unfiltered_transaction_link(auth_client, user):
    CategoryNode.objects.filter(user=user).update(income_dashboard_role='')
    response = auth_client.get(reverse('core:income_salary_dashboard'))
    assert 'No income categories are assigned to this dashboard' in response.content.decode()
    assert '/transactions/?group=income&category=' not in response.content.decode()


def test_backup_preserves_renamed_assignment(user):
    from core.services.user_data_io import export_user_data, import_user_data
    node = CategoryNode.objects.create(user=user, group=CategoryGroup.get_group('income'),
                                       name='Renamed Paycheck', income_dashboard_role='salary')
    backup = export_user_data(user)
    restored = UserFactory()
    import_user_data(restored, backup)
    assert CategoryNode.objects.get(user=restored, name=node.name).income_dashboard_role == 'salary'


def test_default_tree_seeds_roles_and_overview_excludes_renamed_extras(user):
    from core.services.stats import get_dashboard_stats
    CategoryNode.load_default_tree(user)
    roles = income_category_roles(user)
    assert all(roles[role] for role in ('salary', 'bonus', 'association', 'government', 'bank', 'reimbursement'))
    salary = CategoryNode.objects.get(pk=next(iter(roles['salary'])))
    bonus = CategoryNode.objects.get(pk=next(iter(roles['bonus'])))
    LogicalTransactionFactory(user=user, category_v2=salary, date=date(2025, 2, 1),
                              amount_crc=100, amount=100)
    LogicalTransactionFactory(user=user, category_v2=bonus, date=date(2025, 2, 1),
                              amount_crc=1000, amount=1000)
    before = get_dashboard_stats(user)
    bonus.name = 'Renamed Extra'
    bonus.save()
    after = get_dashboard_stats(user)
    assert before['summary'] == after['summary']


def test_migration_assigns_legacy_and_renamed_categories(user):
    import importlib
    from django.apps import apps
    from django.db import connection
    from types import SimpleNamespace
    group = CategoryGroup.get_group('income')
    work = CategoryNode.objects.create(user=user, group=group, name='Work')
    salary = CategoryNode.objects.create(user=user, group=group, name='Salary', parent=work)
    bonus = CategoryNode.objects.create(user=user, group=group, name='Work Bonuses')
    bank = CategoryNode.objects.create(user=user, group=group, name='Bank')
    migration = importlib.import_module('core.migrations.0017_category_income_dashboard_role')
    migration.assign_roles(apps, SimpleNamespace(connection=connection))
    for node, role in ((salary, 'salary'), (bonus, 'bonus'), (bank, 'bank')):
        node.refresh_from_db()
        assert node.income_dashboard_role == role
