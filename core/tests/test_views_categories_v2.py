"""Integration tests for the Categories V2 (hierarchical) views."""
from django.urls import reverse

from core.models import CategoryGroup, CategoryNode, User


def make_node(user, name, parent=None, group_slug='expense'):
    return CategoryNode.objects.create(
        name=name, user=user, parent=parent, group=CategoryGroup.get_group(group_slug),
    )


class TestCategoryV2List:
    def test_empty_list_renders(self, auth_client, category_groups):
        resp = auth_client.get(reverse('core:category_v2_list'))
        assert resp.status_code == 200

    def test_renders_nested_nodes(self, auth_client, user, category_groups):
        food = make_node(user, 'Food')
        make_node(user, 'Groceries', parent=food)
        content = auth_client.get(reverse('core:category_v2_list')).content.decode()
        assert 'Food' in content and 'Groceries' in content

    def test_requires_login(self, client):
        resp = client.get(reverse('core:category_v2_list'))
        assert resp.status_code == 302

    def test_only_shows_own_nodes(self, auth_client, category_groups):
        other = User.objects.create_user(email='other@example.com', password='x')
        make_node(other, 'SecretCategory')
        content = auth_client.get(reverse('core:category_v2_list')).content.decode()
        assert 'SecretCategory' not in content


class TestCategoryV2Save:
    def test_create_root(self, auth_client, user, category_groups):
        resp = auth_client.post(reverse('core:category_v2_save'), {
            'name': 'Food', 'group': 'expense', 'color': '#112233',
        })
        assert resp.status_code == 302
        node = CategoryNode.objects.get(user=user, name='Food')
        assert node.parent is None and node.color == '#112233'
        assert node.group.slug == 'expense'

    def test_create_child_inherits_parent_group(self, auth_client, user, category_groups):
        income = make_node(user, 'Work', group_slug='income')
        auth_client.post(reverse('core:category_v2_save'), {
            'name': 'Salary', 'group': 'expense', 'parent': income.pk,
        })
        assert CategoryNode.objects.get(name='Salary').group.slug == 'income'

    def test_rejects_unclassified_group(self, auth_client, user, category_groups):
        auth_client.post(reverse('core:category_v2_save'), {'name': 'X', 'group': 'unclassified'})
        assert not CategoryNode.objects.filter(name='X').exists()

    def test_rejects_duplicate_name(self, auth_client, user, category_groups):
        make_node(user, 'Food')
        auth_client.post(reverse('core:category_v2_save'), {'name': 'Food', 'group': 'expense'})
        assert CategoryNode.objects.filter(user=user, name='Food').count() == 1

    def test_rejects_invalid_color(self, auth_client, user, category_groups):
        auth_client.post(reverse('core:category_v2_save'), {
            'name': 'Food', 'group': 'expense', 'color': 'red',
        })
        assert not CategoryNode.objects.filter(name='Food').exists()

    def test_cannot_use_other_users_parent(self, auth_client, user, category_groups):
        other = User.objects.create_user(email='other@example.com', password='x')
        foreign = make_node(other, 'Foreign')
        resp = auth_client.post(reverse('core:category_v2_save'), {
            'name': 'Child', 'group': 'expense', 'parent': foreign.pk,
        })
        assert resp.status_code == 404
        assert not CategoryNode.objects.filter(name='Child').exists()

    def test_rename_and_move(self, auth_client, user, category_groups):
        food = make_node(user, 'Food')
        groceries = make_node(user, 'Groceries')
        auth_client.post(reverse('core:category_v2_save'), {
            'id': groceries.pk, 'name': 'Supermarket', 'color': '#abcdef', 'parent': food.pk,
        })
        groceries.refresh_from_db()
        assert groceries.name == 'Supermarket'
        assert groceries.parent == food

    def test_cannot_move_under_descendant(self, auth_client, user, category_groups):
        parent = make_node(user, 'Parent')
        child = make_node(user, 'Child', parent=parent)
        auth_client.post(reverse('core:category_v2_save'), {
            'id': parent.pk, 'name': 'Parent', 'parent': child.pk,
        })
        parent.refresh_from_db()
        assert parent.parent is None

    def test_cannot_edit_other_users_node(self, auth_client, category_groups):
        other = User.objects.create_user(email='other@example.com', password='x')
        node = make_node(other, 'Theirs')
        resp = auth_client.post(reverse('core:category_v2_save'), {'id': node.pk, 'name': 'Mine'})
        assert resp.status_code == 404
        node.refresh_from_db()
        assert node.name == 'Theirs'


class TestCategoryV2Group:
    def post(self, client, ids, name='Food', **extra):
        return client.post(reverse('core:category_v2_group'), {'ids': ids, 'name': name, **extra})

    def test_groups_root_nodes_under_new_root(self, auth_client, user, category_groups):
        a = make_node(user, 'Groceries')
        b = make_node(user, 'Eating Out')
        self.post(auth_client, [a.pk, b.pk])
        food = CategoryNode.objects.get(user=user, name='Food')
        a.refresh_from_db(); b.refresh_from_db()
        assert food.parent is None and food.group.slug == 'expense'
        assert a.parent == food and b.parent == food

    def test_new_parent_takes_selected_nodes_parent(self, auth_client, user, category_groups):
        top = make_node(user, 'Living')
        a = make_node(user, 'Groceries', parent=top)
        b = make_node(user, 'Eating Out', parent=top)
        self.post(auth_client, [a.pk, b.pk], color='#123456')
        food = CategoryNode.objects.get(name='Food')
        assert food.parent == top and food.color == '#123456'

    def test_requires_two_nodes(self, auth_client, user, category_groups):
        a = make_node(user, 'Groceries')
        self.post(auth_client, [a.pk])
        assert not CategoryNode.objects.filter(name='Food').exists()

    def test_rejects_different_levels(self, auth_client, user, category_groups):
        top = make_node(user, 'Living')
        child = make_node(user, 'Groceries', parent=top)
        other = make_node(user, 'Other')
        self.post(auth_client, [child.pk, other.pk])
        assert not CategoryNode.objects.filter(name='Food').exists()

    def test_rejects_different_groups(self, auth_client, user, category_groups):
        a = make_node(user, 'Groceries')
        b = make_node(user, 'Salary', group_slug='income')
        self.post(auth_client, [a.pk, b.pk])
        assert not CategoryNode.objects.filter(name='Food').exists()

    def test_ignores_other_users_nodes(self, auth_client, user, category_groups):
        other = User.objects.create_user(email='other@example.com', password='x')
        mine = make_node(user, 'Groceries')
        theirs = make_node(other, 'Eating Out')
        self.post(auth_client, [mine.pk, theirs.pk])
        theirs.refresh_from_db()
        assert theirs.parent is None
        assert not CategoryNode.objects.filter(name='Food').exists()

    def test_duplicate_parent_name_rolls_back(self, auth_client, user, category_groups):
        make_node(user, 'Food')
        a = make_node(user, 'Groceries')
        b = make_node(user, 'Eating Out')
        self.post(auth_client, [a.pk, b.pk])
        a.refresh_from_db(); b.refresh_from_db()
        assert a.parent is None and b.parent is None
        assert CategoryNode.objects.filter(name='Food').count() == 1


class TestCategoryV2Move:
    def post(self, client, ids, parent=''):
        return client.post(reverse('core:category_v2_move'), {'ids': ids, 'parent': parent})

    def test_moves_siblings_under_parent(self, auth_client, user, category_groups):
        target = make_node(user, 'Food')
        a = make_node(user, 'Groceries')
        b = make_node(user, 'Eating Out')
        self.post(auth_client, [a.pk, b.pk], target.pk)
        a.refresh_from_db(); b.refresh_from_db()
        assert a.parent == target and b.parent == target

    def test_moves_to_top_level(self, auth_client, user, category_groups):
        top = make_node(user, 'Food')
        a = make_node(user, 'Groceries', parent=top)
        b = make_node(user, 'Eating Out', parent=top)
        self.post(auth_client, [a.pk, b.pk], '')
        a.refresh_from_db(); b.refresh_from_db()
        assert a.parent is None and b.parent is None

    def test_moves_a_subtree_intact(self, auth_client, user, category_groups):
        target = make_node(user, 'Food')
        a = make_node(user, 'Groceries')
        leaf = make_node(user, 'Produce', parent=a)
        self.post(auth_client, [a.pk], target.pk)
        a.refresh_from_db(); leaf.refresh_from_db()
        assert a.parent == target and leaf.parent == a

    def test_cannot_move_under_own_descendant(self, auth_client, user, category_groups):
        a = make_node(user, 'Groceries')
        child = make_node(user, 'Produce', parent=a)
        self.post(auth_client, [a.pk], child.pk)
        a.refresh_from_db()
        assert a.parent is None

    def test_cannot_move_under_a_selected_node(self, auth_client, user, category_groups):
        a = make_node(user, 'Groceries')
        b = make_node(user, 'Eating Out')
        self.post(auth_client, [a.pk, b.pk], a.pk)
        a.refresh_from_db(); b.refresh_from_db()
        assert a.parent is None and b.parent is None

    def test_cannot_move_across_groups(self, auth_client, user, category_groups):
        income = make_node(user, 'Work', group_slug='income')
        a = make_node(user, 'Groceries')
        self.post(auth_client, [a.pk], income.pk)
        a.refresh_from_db()
        assert a.parent is None

    def test_cannot_use_other_users_parent_or_nodes(self, auth_client, user, category_groups):
        other = User.objects.create_user(email='other@example.com', password='x')
        theirs = make_node(other, 'Theirs')
        mine = make_node(user, 'Mine')
        assert self.post(auth_client, [mine.pk], theirs.pk).status_code == 404
        self.post(auth_client, [mine.pk, theirs.pk])
        theirs.refresh_from_db()
        assert theirs.parent is None

    def test_failure_rolls_back_all_moves(self, auth_client, user, category_groups):
        target = make_node(user, 'Food')
        make_node(user, 'Groceries', parent=target)
        a = make_node(user, 'Groceries2')
        # The second node is the target's descendant, so the move is invalid and must not apply partially.
        child = CategoryNode.objects.get(name='Groceries')
        self.post(auth_client, [a.pk, target.pk], child.pk)
        a.refresh_from_db(); target.refresh_from_db()
        assert a.parent is None and target.parent is None


class TestCategoryV2ImportV1:
    def test_imports_v1_as_top_level_and_is_idempotent(self, auth_client, user, category_groups):
        from core.models import Category, CategoryGroup
        expense = CategoryGroup.objects.get(slug='expense')
        unclassified = CategoryGroup.objects.get(slug=CategoryGroup.UNCLASSIFIED)
        Category.objects.create(name='Rent', group=expense, user=user, color='#112233')
        Category.objects.get_or_create(name='Unclassified', group=unclassified, user=user)
        make_node(user, 'Existing')
        Category.objects.create(name='Existing', group=expense, user=user)

        url = reverse('core:category_v2_import_v1')
        v1_count = Category.objects.filter(user=user).count()
        auth_client.post(url)
        auth_client.post(url)

        rent = CategoryNode.objects.get(user=user, name='Rent')
        assert rent.parent is None and rent.color == '#112233' and rent.group == expense
        assert CategoryNode.objects.filter(user=user, name='Existing').count() == 1
        assert not CategoryNode.objects.filter(name='Unclassified').exists()
        assert Category.objects.filter(user=user).count() == v1_count


class TestCategoryV2Delete:
    def test_delete_leaf(self, auth_client, user, category_groups):
        node = make_node(user, 'Food')
        auth_client.post(reverse('core:category_v2_delete'), {'id': node.pk})
        assert not CategoryNode.objects.filter(pk=node.pk).exists()

    def test_delete_with_children_is_blocked(self, auth_client, user, category_groups):
        parent = make_node(user, 'Food')
        make_node(user, 'Groceries', parent=parent)
        auth_client.post(reverse('core:category_v2_delete'), {'id': parent.pk})
        assert CategoryNode.objects.filter(pk=parent.pk).exists()

    def test_bulk_delete(self, auth_client, user, category_groups):
        a, b = make_node(user, 'A'), make_node(user, 'B')
        auth_client.post(reverse('core:category_v2_delete'), {'ids': [a.pk, b.pk]})
        assert not CategoryNode.objects.filter(pk__in=[a.pk, b.pk]).exists()

    def test_bulk_delete_is_all_or_nothing(self, auth_client, user, category_groups):
        parent = make_node(user, 'Food')
        make_node(user, 'Groceries', parent=parent)
        leaf = make_node(user, 'Other')
        auth_client.post(reverse('core:category_v2_delete'), {'ids': [parent.pk, leaf.pk]})
        assert CategoryNode.objects.filter(pk__in=[parent.pk, leaf.pk]).count() == 2

    def test_bulk_delete_with_foreign_id_404s(self, auth_client, user, category_groups):
        other = User.objects.create_user(email='other@example.com', password='x')
        mine, theirs = make_node(user, 'Mine'), make_node(other, 'Theirs')
        resp = auth_client.post(reverse('core:category_v2_delete'), {'ids': [mine.pk, theirs.pk]})
        assert resp.status_code == 404
        assert CategoryNode.objects.filter(pk=mine.pk).exists()

    def test_cannot_delete_other_users_node(self, auth_client, category_groups):
        other = User.objects.create_user(email='other@example.com', password='x')
        node = make_node(other, 'Theirs')
        resp = auth_client.post(reverse('core:category_v2_delete'), {'id': node.pk})
        assert resp.status_code == 404
        assert CategoryNode.objects.filter(pk=node.pk).exists()
