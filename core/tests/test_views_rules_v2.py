from decimal import Decimal

import pytest
from django.urls import reverse

from core.models import CategoryGroup, CategoryNode, ClassificationRuleV2, User


def make_node(user, name, slug='expense'):
    return CategoryNode.objects.create(name=name, user=user, group=CategoryGroup.get_group(slug))


@pytest.mark.django_db
class TestRulesV2Views:
    def test_old_category_rules_url_redirects_to_category_rule_manager(self, auth_client, user, category_groups):
        food = make_node(user, 'Food')
        response = auth_client.get(reverse('core:rules_v2_list'), {'node': food.pk})
        assert response.status_code == 302
        assert response.url == reverse('core:category_rules_list', args=[food.pk])
        assert auth_client.get(reverse('core:rules_v2_list')).url == reverse('core:category_v2_list')

    def test_category_rules_page_lists_only_direct_rules(self, auth_client, user, category_groups):
        food = make_node(user, 'Food')
        child = CategoryNode.objects.create(name='Groceries', user=user, group=food.group, parent=food)
        ClassificationRuleV2.objects.create(category=food, user=user, description='GROCERY STORE')
        ClassificationRuleV2.objects.create(category=child, user=user, description='MARKET')

        response = auth_client.get(reverse('core:category_rules_list', args=[food.pk]))
        html = response.content.decode()

        assert response.status_code == 200
        assert response.context['managed_category'] == food
        assert [rule.description for rule in response.context['managed_rules']] == ['GROCERY STORE']
        assert 'GROCERY STORE' in html and 'MARKET' not in html
        assert 'not inherited by subcategories' in html
        assert 'id="categoryRulesModal"' not in html

    def test_save_keeps_selected_node(self, auth_client, user, category_groups):
        food = make_node(user, 'Food')
        resp = auth_client.post(reverse('core:rules_v2_save'), {
            'category': food.pk, 'description': 'X', 'selected_node': food.pk})
        assert resp.url.endswith(f'/rules/{food.pk}/')

    def test_rule_save_returns_to_category_rule_manager(self, auth_client, user, category_groups):
        food = make_node(user, 'Food')
        next_url = reverse('core:category_rules_list', args=[food.pk])
        response = auth_client.post(reverse('core:rules_v2_save'), {
            'category': food.pk, 'description': 'GROCER', 'next': next_url,
        })
        assert response.url == next_url

    def test_apply_unclassified_button_preserves_selection(self, auth_client, user, category_groups):
        from html import escape
        from core.tests.factories import LogicalTransactionFactory, UserFactory

        food = make_node(user, 'Food')
        other = make_node(user, 'Other')
        rule = ClassificationRuleV2.objects.create(category=food, user=user, description='MATCH')
        pending = LogicalTransactionFactory(user=user, description='MATCH', classification_method_v2='unclassified')
        manual = LogicalTransactionFactory(user=user, description='MATCH', category_v2=other,
                                           classification_method_v2='manual')
        existing = LogicalTransactionFactory(user=user, description='MATCH', category_v2=other,
                                             classification_method_v2='rule')
        unmatched = LogicalTransactionFactory(user=user, description='NO HIT',
                                              classification_method_v2='unclassified')
        other_user = UserFactory()
        theirs = LogicalTransactionFactory(user=other_user, description='MATCH',
                                           classification_method_v2='unclassified')
        next_url = reverse('core:category_v2_list')
        page = auth_client.get(next_url)
        html = page.content.decode()
        assert f'action="{reverse("core:classify_unclassified")}"' in html
        assert f'name="next" value="{escape(next_url, quote=True)}"' in html
        assert 'Apply Rules to Unclassified' in html
        response = auth_client.post(reverse('core:classify_unclassified'), {'next': next_url}, follow=True)
        assert response.redirect_chain == [(next_url, 302)]
        assert 'Rules applied: 1 transactions classified. 1 remain unclassified.' in response.content.decode()
        pending.refresh_from_db()
        assert pending.category_v2 == food and pending.matched_rule_v2 == rule
        assert pending.classification_method_v2 == 'rule'
        for txn, method in ((manual, 'manual'), (existing, 'rule'), (unmatched, 'unclassified'),
                            (theirs, 'unclassified')):
            txn.refresh_from_db()
            assert txn.classification_method_v2 == method
        assert manual.category_v2 == other and existing.category_v2 == other

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

    def test_changing_rule_category_moves_its_rule_classified_transactions(self, auth_client, user, category_groups):
        from core.tests.factories import LogicalTransactionFactory

        food, fun = make_node(user, 'Food'), make_node(user, 'Fun')
        rule = ClassificationRuleV2.objects.create(category=food, user=user, description='A')
        other_rule = ClassificationRuleV2.objects.create(category=food, user=user, description='Z')
        mine = LogicalTransactionFactory(user=user, description='A', category_v2=food,
                                         matched_rule_v2=rule, classification_method_v2='rule')
        manual = LogicalTransactionFactory(user=user, description='A', category_v2=food,
                                           matched_rule_v2=rule, classification_method_v2='manual')
        elsewhere = LogicalTransactionFactory(user=user, description='Z', category_v2=food,
                                              matched_rule_v2=other_rule, classification_method_v2='rule')
        resp = auth_client.post(reverse('core:rules_v2_save'),
                                {'id': rule.pk, 'category': fun.pk, 'description': 'A'}, follow=True)
        assert '1 transaction moved to Fun.' in resp.content.decode()
        for txn, expected in ((mine, fun), (manual, food), (elsewhere, food)):
            txn.refresh_from_db()
            assert txn.category_v2 == expected

    def test_save_assigns_tags_and_tags_existing_matches(self, auth_client, user, category_groups):
        from core.models import Tag
        from core.tests.factories import LogicalTransactionFactory

        food = make_node(user, 'Food')
        tag = Tag.objects.create(user=user, name='Trip')
        rule = ClassificationRuleV2.objects.create(category=food, user=user, description='A')
        txn = LogicalTransactionFactory(user=user, description='A', category_v2=food,
                                        matched_rule_v2=rule, classification_method_v2='rule')
        auth_client.post(reverse('core:rules_v2_save'),
                         {'id': rule.pk, 'category': food.pk, 'description': 'A', 'tags': [tag.pk]})
        assert list(rule.tags.all()) == [tag]
        assert list(txn.tags.all()) == [tag]
        auth_client.post(reverse('core:rules_v2_save'), {'id': rule.pk, 'category': food.pk, 'description': 'A'})
        assert rule.tags.count() == 0

    def test_save_rejects_other_users_tags(self, auth_client, user, category_groups):
        from core.models import Tag

        food = make_node(user, 'Food')
        other = User.objects.create_user(email='o@example.com', password='x')
        theirs = Tag.objects.create(user=other, name='Theirs')
        auth_client.post(reverse('core:rules_v2_save'), {'category': food.pk, 'description': 'A', 'tags': [theirs.pk]})
        assert not ClassificationRuleV2.objects.filter(user=user).exists()

    def test_category_rule_manager_and_transactions_render_rule_tags(self, auth_client, user, category_groups):
        from core.models import Tag

        food = make_node(user, 'Food')
        rule = ClassificationRuleV2.objects.create(category=food, user=user, description='A')
        rule.tags.add(Tag.objects.create(user=user, name='Trip'))
        manager_url = reverse('core:category_rules_list', args=[food.pk])
        assert 'Trip' in auth_client.get(manager_url).content.decode()
        assert auth_client.get(reverse('core:transaction_list')).status_code == 200

    def test_editing_rule_without_category_change_moves_nothing(self, auth_client, user, category_groups):
        from core.tests.factories import LogicalTransactionFactory

        food = make_node(user, 'Food')
        rule = ClassificationRuleV2.objects.create(category=food, user=user, description='A')
        txn = LogicalTransactionFactory(user=user, description='A', category_v2=food,
                                        matched_rule_v2=rule, classification_method_v2='rule')
        resp = auth_client.post(reverse('core:rules_v2_save'),
                                {'id': rule.pk, 'category': food.pk, 'description': 'B'}, follow=True)
        assert '1 transaction moved' not in resp.content.decode()
        txn.refresh_from_db()
        assert txn.category_v2 == food

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

    def test_requires_login(self, client, db):
        assert client.get(reverse('core:rules_v2_list')).status_code == 302

import pytest as _pytest


@_pytest.mark.django_db
class TestAccountDeleteAllV2:
    def _setup(self, user):
        food = make_node(user, 'Food')
        kid = CategoryNode.objects.create(name='Snacks', user=user, group=food.group, parent=food)
        rule = ClassificationRuleV2.objects.create(category=kid, user=user, description='CANDY')
        return food, kid, rule

    def _txn(self, user, node, rule, method):
        from core.models import LogicalTransaction
        t = LogicalTransaction.objects.filter(user=user).first()
        t.category_v2, t.matched_rule_v2, t.classification_method_v2 = node, rule, method
        t.save()
        return t

    def test_delete_all_rules_resets_transactions(self, auth_client, user, sample_data, category_groups):
        food, kid, rule = self._setup(user)
        t = self._txn(user, kid, rule, 'rule')
        auth_client.post(reverse('core:delete_all_rules'))
        t.refresh_from_db()
        assert not ClassificationRuleV2.objects.filter(user=user).exists()
        assert t.classification_method_v2 == 'unclassified' and t.category_v2.name == 'Unclassified'

    def test_delete_all_categories_removes_tree_keeps_protected(self, auth_client, user, sample_data, category_groups):
        food, kid, rule = self._setup(user)
        t = self._txn(user, kid, rule, 'manual')
        auth_client.post(reverse('core:yaml_category_delete_all'))
        t.refresh_from_db()
        names = set(CategoryNode.objects.filter(user=user).values_list('name', flat=True))
        assert names == {'Unclassified'}
        assert not ClassificationRuleV2.objects.filter(user=user).exists()
        assert t.category_v2.name == 'Unclassified' and t.classification_method_v2 == 'unclassified'

    def test_delete_all_categories_single_group(self, auth_client, user, sample_data, category_groups):
        food, kid, rule = self._setup(user)
        income = make_node(user, 'Salary', 'income')
        auth_client.post(reverse('core:yaml_category_delete_all'), {'group': 'expense'})
        assert CategoryNode.objects.filter(pk=income.pk).exists()
        assert not CategoryNode.objects.filter(pk__in=[food.pk, kid.pk]).exists()


@_pytest.mark.django_db
class TestTransactionsPageClassifyActions:
    def _prep(self, user):
        from core.models import LogicalTransaction
        LogicalTransaction.objects.filter(user=user).update(
            category_v2=None, matched_rule_v2=None, classification_method_v2='unclassified',
        )
        ClassificationRuleV2.objects.filter(user=user).delete()
        node = make_node(user, 'Food')
        txns = list(LogicalTransaction.objects.filter(user=user).order_by('pk'))
        ClassificationRuleV2.objects.create(category=node, user=user, description=txns[0].description)
        return node, txns

    def test_reclassify_all_keeps_manual(self, auth_client, user, sample_data, category_groups):
        node, txns = self._prep(user)
        manual = txns[-1]
        manual.category_v2, manual.classification_method_v2 = node, 'manual'
        manual.save()
        auth_client.post(reverse('core:reclassify_all'))
        first = type(txns[0]).objects.get(pk=txns[0].pk)
        assert first.category_v2 == node and first.classification_method_v2 == 'rule'
        manual.refresh_from_db()
        assert manual.classification_method_v2 == 'manual'

    def test_classify_unclassified_and_clear(self, auth_client, user, sample_data, category_groups):
        node, txns = self._prep(user)
        auth_client.post(reverse('core:classify_unclassified'))
        first = type(txns[0]).objects.get(pk=txns[0].pk)
        assert first.category_v2 == node
        auth_client.post(reverse('core:clear_classifications'), {'method': 'rule'})
        first.refresh_from_db()
        assert first.category_v2.name == 'Unclassified' and first.classification_method_v2 == 'unclassified'
