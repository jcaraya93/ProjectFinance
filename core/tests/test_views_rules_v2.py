from decimal import Decimal

import pytest
from django.urls import reverse

from core.models import Category, CategoryGroup, CategoryNode, ClassificationRule, ClassificationRuleV2, User


def make_node(user, name, slug='expense'):
    return CategoryNode.objects.create(name=name, user=user, group=CategoryGroup.get_group(slug))


@pytest.mark.django_db
class TestRulesV2Views:
    def test_list_renders_rules_grouped_by_category(self, auth_client, user, category_groups):
        food = make_node(user, 'Food')
        ClassificationRuleV2.objects.create(category=food, user=user, description='SUPERMARKET')
        resp = auth_client.get(reverse('core:rules_v2_list'))
        html = resp.content.decode()
        assert resp.status_code == 200
        assert 'SUPERMARKET' in html and 'Food' in html

    def test_node_filter_includes_subtree_only(self, auth_client, user, category_groups):
        food = make_node(user, 'Food')
        kid = CategoryNode.objects.create(name='Snacks', user=user, group=food.group, parent=food)
        other = make_node(user, 'Rent')
        ClassificationRuleV2.objects.create(category=food, user=user, description='GROCER')
        ClassificationRuleV2.objects.create(category=kid, user=user, description='CANDY')
        ClassificationRuleV2.objects.create(category=other, user=user, description='LANDLORD')
        url = reverse('core:rules_v2_list')
        html = auth_client.get(url, {'node': food.pk}).content.decode()
        assert 'GROCER' in html and 'CANDY' in html and 'LANDLORD' not in html
        html = auth_client.get(url, {'node': kid.pk}).content.decode()
        assert 'CANDY' in html and 'GROCER' not in html
        assert 'LANDLORD' in auth_client.get(url).content.decode()

    def test_save_keeps_selected_node(self, auth_client, user, category_groups):
        food = make_node(user, 'Food')
        resp = auth_client.post(reverse('core:rules_v2_save'), {
            'category': food.pk, 'description': 'X', 'selected_node': food.pk})
        assert resp.url.endswith(f'?node={food.pk}')

    def test_create_rule(self, auth_client, user, category_groups):
        food = make_node(user, 'Food')
        auth_client.post(reverse('core:rules_v2_save'), {
            'category': food.pk, 'description': 'SUPER', 'account_type': 'credit_account',
            'amount_min': '5.50', 'metadata': 'code=PT\nother = x', 'detail': 'note',
        })
        rule = ClassificationRuleV2.objects.get(user=user)
        assert rule.category == food and rule.description == 'SUPER'
        assert rule.amount_min == Decimal('5.50') and rule.metadata == {'code': 'PT', 'other': 'x'}

    def test_edit_rule(self, auth_client, user, category_groups):
        food, fun = make_node(user, 'Food'), make_node(user, 'Fun')
        rule = ClassificationRuleV2.objects.create(category=food, user=user, description='A')
        auth_client.post(reverse('core:rules_v2_save'), {'id': rule.pk, 'category': fun.pk, 'description': 'B'})
        rule.refresh_from_db()
        assert rule.category == fun and rule.description == 'B'

    def test_invalid_input_is_rejected(self, auth_client, user, category_groups):
        food = make_node(user, 'Food')
        url = reverse('core:rules_v2_save')
        auth_client.post(url, {'category': food.pk})  # no conditions
        auth_client.post(url, {'category': food.pk, 'description': 'x', 'amount_min': 'abc'})
        auth_client.post(url, {'category': food.pk, 'description': 'x', 'metadata': 'novalue'})
        auth_client.post(url, {'category': food.pk, 'description': 'x', 'account_type': 'bogus'})
        auth_client.post(url, {'category': food.pk, 'amount_min': '9', 'amount_max': '1'})
        assert not ClassificationRuleV2.objects.exists()

    def test_cannot_use_or_touch_other_users_data(self, auth_client, user, category_groups):
        other = User.objects.create_user(email='other@example.com', password='x')
        theirs = make_node(other, 'Theirs')
        their_rule = ClassificationRuleV2.objects.create(category=theirs, user=other, description='x')
        mine = make_node(user, 'Mine')
        assert auth_client.post(reverse('core:rules_v2_save'), {'category': theirs.pk, 'description': 'x'}).status_code == 404
        assert auth_client.post(reverse('core:rules_v2_save'), {
            'id': their_rule.pk, 'category': mine.pk, 'description': 'hijack'}).status_code == 404
        assert auth_client.post(reverse('core:rules_v2_delete'), {'id': their_rule.pk}).status_code == 404
        their_rule.refresh_from_db()
        assert their_rule.description == 'x'

    def test_delete_rule(self, auth_client, user, category_groups):
        rule = ClassificationRuleV2.objects.create(category=make_node(user, 'Food'), user=user, description='x')
        auth_client.post(reverse('core:rules_v2_delete'), {'id': rule.pk})
        assert not ClassificationRuleV2.objects.exists()

    def test_import_from_v1(self, auth_client, user, category_groups):
        expense = CategoryGroup.get_group(CategoryGroup.EXPENSE)
        v1 = Category.objects.create(name='Food', group=expense, user=user)
        ClassificationRule.objects.create(category=v1, user=user, description='SUPER')
        make_node(user, 'Food')
        url = reverse('core:rules_v2_import_v1')
        auth_client.post(url)
        auth_client.post(url)
        assert ClassificationRuleV2.objects.filter(user=user).count() == 1
        assert ClassificationRule.objects.filter(user=user).count() == 1

    def test_requires_login(self, client, db):
        assert client.get(reverse('core:rules_v2_list')).status_code == 302
