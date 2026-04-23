import django_filters
from django.db.models import Q

from .models import LogicalTransaction, Category, Account


class TransactionFilter(django_filters.FilterSet):
    start_date = django_filters.DateFilter(field_name='date', lookup_expr='gte')
    end_date = django_filters.DateFilter(field_name='date', lookup_expr='lte')
    search = django_filters.CharFilter(field_name='description', lookup_expr='icontains')
    category = django_filters.ModelMultipleChoiceFilter(
        field_name='category',
        queryset=Category.objects.none(),
    )
    group = django_filters.MultipleChoiceFilter(
        field_name='category__group__slug',
        choices=[],
    )
    cls_method = django_filters.MultipleChoiceFilter(
        field_name='classification_method',
        choices=LogicalTransaction.CLASSIFICATION_METHODS,
    )
    wallet = django_filters.CharFilter(method='filter_wallets')
    amount_min = django_filters.NumberFilter(field_name='amount', lookup_expr='gte')
    amount_max = django_filters.NumberFilter(field_name='amount', lookup_expr='lte')
    rule = django_filters.NumberFilter(field_name='matched_rule_id')
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
        if user:
            self.filters['category'].queryset = Category.objects.filter(user=user)
        from .models import CategoryGroup
        self.filters['group'].extra['choices'] = list(CategoryGroup.SLUG_CHOICES)

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
        qs = super().filter_queryset(queryset)

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
