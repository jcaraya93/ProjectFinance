import pytest
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError

from core.models import CategoryGroup, CategoryNode, User


@pytest.mark.django_db
class TestCategoryNode:
    def setup_method(self):
        self.user = User.objects.create_user(email='hierarchy@example.com', password='testpass123!')
        self.expense = CategoryGroup.get_group(CategoryGroup.EXPENSE)

    def make_node(self, name, parent=None, user=None, group=None):
        return CategoryNode.objects.create(
            name=name,
            parent=parent,
            user=user or self.user,
            group=group or self.expense,
        )

    def test_supports_arbitrary_depth_and_identifies_leaf_nodes(self):
        food = self.make_node('Food')
        groceries = self.make_node('Groceries', parent=food)
        produce = self.make_node('Produce', parent=groceries)

        food.refresh_from_db()
        groceries.refresh_from_db()
        assert not food.is_leaf
        assert not groceries.is_leaf
        assert produce.is_leaf

    def test_rejects_parent_from_another_user_or_group(self):
        other_user = User.objects.create_user(email='other@example.com', password='testpass123!')
        other_group = CategoryGroup.get_group(CategoryGroup.INCOME)
        foreign_parent = self.make_node('Foreign', user=other_user, group=other_group)

        with pytest.raises(ValidationError, match='same user and group'):
            self.make_node('Child', parent=foreign_parent)

    def test_rejects_cycles(self):
        parent = self.make_node('Parent')
        child = self.make_node('Child', parent=parent)

        parent.parent = child
        with pytest.raises(ValidationError, match='descendant'):
            parent.save()

    def test_category_names_remain_unique_per_user_and_group(self):
        self.make_node('Food')
        with pytest.raises(ValidationError):
            self.make_node('Food')

    def test_parent_cannot_be_deleted_while_it_has_children(self):
        parent = self.make_node('Parent')
        self.make_node('Child', parent=parent)

        with pytest.raises(ProtectedError):
            parent.delete()
