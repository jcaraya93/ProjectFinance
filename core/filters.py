import django_filters
from django.db.models import Q

from .models import CategoryNode, ClassificationRule, ClassificationRuleV2, LogicalTransaction


class TransactionFilter(django_filters.FilterSet):
    start_date = django_filters.DateFilter(field_name='date', lookup_expr='gte')
    end_date = django_filters.DateFilter(field_name='date', lookup_expr='lte')
    search = django_filters.CharFilter(field_name='description', lookup_expr='icontains')
    # Categories V2: a selected category also matches transactions in its subcategories.
    category = django_filters.ModelMultipleChoiceFilter(
        queryset=CategoryNode.objects.none(),
        method='filter_category',
    )
    group = django_filters.MultipleChoiceFilter(
        field_name='category_v2__group__slug',
        choices=[],
    )
    cls_method = django_filters.MultipleChoiceFilter(
        field_name='classification_method_v2',
        choices=LogicalTransaction.CLASSIFICATION_METHODS,
    )
    wallet = django_filters.CharFilter(method='filter_wallets')
    amount_min = django_filters.NumberFilter(field_name='amount', lookup_expr='gte')
    amount_max = django_filters.NumberFilter(field_name='amount', lookup_expr='lte')
    rule = django_filters.NumberFilter(field_name='matched_rule_v2_id')
    statement = django_filters.NumberFilter(
        field_name='raw_transaction__ledger__statement_import_id',
    )
    transaction_code = django_filters.CharFilter(
        field_name='raw_transaction__account_metadata__transaction_code',
    )
    reference_number = django_filters.CharFilter(
        field_name='raw_transaction__account_metadata__reference_number',
    )
    meta = django_filters.CharFilter(method='filter_metadata')

    class Meta:
        model = LogicalTransaction
        fields = []

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user:
            self.filters['category'].queryset = CategoryNode.objects.filter(user=user)
        from .models import CategoryGroup
        self.filters['group'].extra['choices'] = list(CategoryGroup.SLUG_CHOICES)

    def filter_category(self, queryset, name, value):
        selected = {node.pk for node in value}
        if not selected:
            return queryset
        return queryset.filter(category_v2_id__in=self._with_descendants(selected))

    def _with_descendants(self, ids):
        children = {}
        for pk, parent_id in CategoryNode.objects.filter(user=self.user).values_list('pk', 'parent_id'):
            children.setdefault(parent_id, []).append(pk)
        result, stack = set(), list(ids)
        while stack:
            pk = stack.pop()
            if pk not in result:
                result.add(pk)
                stack.extend(children.get(pk, []))
        return result

    def _legacy_filters(self, queryset):
        """Links from V1 pages carry V1 ids: v1_category (Category pks) and v1_rule (ClassificationRule pk)."""
        v1_categories = [c for c in self.data.getlist('v1_category') if c.isdigit()]
        if v1_categories:
            from .models import Category
            pairs = Category.objects.filter(user=self.user, pk__in=v1_categories).values_list('group_id', 'name')
            nodes = CategoryNode.objects.filter(user=self.user)
            ids = [n.pk for n in nodes if (n.group_id, n.name) in set(pairs)]
            queryset = queryset.filter(category_v2_id__in=ids)
        v1_rule = self.data.get('v1_rule', '')
        if v1_rule.isdigit():
            rule = ClassificationRule.objects.filter(user=self.user, pk=v1_rule).select_related('category').first()
            ids = []
            if rule:
                ids = [
                    r.pk for r in ClassificationRuleV2.objects.filter(
                        user=self.user, category__name=rule.category.name,
                        category__group_id=rule.category.group_id, description=rule.description,
                        account_type=rule.account_type, amount_min=rule.amount_min, amount_max=rule.amount_max,
                    ) if r.metadata == rule.metadata
                ]
            queryset = queryset.filter(matched_rule_v2_id__in=ids)
        return queryset

    def filter_wallets(self, queryset, name, value):
        """Handle wallet filters (format: 'account_id:currency')."""
        # This is called per-value; for multi-value we override filter_queryset
        return queryset

    def filter_metadata(self, queryset, name, value):
        """Handle metadata key:value filters."""
        return queryset

    def filter_queryset(self, queryset):
        """Override to handle multi-value wallet and metadata filters."""
        # Let django-filter handle all standard filters first
        qs = self._legacy_filters(super().filter_queryset(queryset))

        # Multi-value wallet filter
        wallet_values = self.data.getlist('wallet')
        if wallet_values:
            wallet_q = Q()
            for w in wallet_values:
                parts = w.split(':')
                if len(parts) == 2:
                    wallet_q |= Q(
                        raw_transaction__ledger__statement_import__account_id=parts[0],
                        raw_transaction__ledger__currency=parts[1],
                    )
            if wallet_q:
                qs = qs.filter(wallet_q)

        # Multi-value metadata filters (format: 'key:value')
        meta_values = self.data.getlist('meta')
        if meta_values:
            meta_by_key = {}
            for mf in meta_values:
                if ':' in mf:
                    mk, mv = mf.split(':', 1)
                    meta_by_key.setdefault(mk, []).append(mv)
            for mk, mvs in meta_by_key.items():
                q = Q()
                for mv in mvs:
                    q |= Q(**{f'raw_transaction__account_metadata__{mk}': mv})
                qs = qs.filter(q)

        return qs
