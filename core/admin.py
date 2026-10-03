from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, Account, CreditAccount, DebitAccount, CategoryGroup, CategoryNode, ClassificationRuleV2, CurrencyLedger, StatementImport, RawTransaction, LogicalTransaction, Transaction, TransactionPair


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['email', 'is_active', 'is_staff', 'date_joined']
    search_fields = ['email']
    ordering = ['email']
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
    )
    add_fieldsets = (
        (None, {'classes': ('wide',), 'fields': ('email', 'password1', 'password2')}),
    )


@admin.register(CreditAccount)
class CreditAccountAdmin(admin.ModelAdmin):
    list_display = ['card_number_last4', 'card_holder', 'nickname']
    search_fields = ['card_holder', 'nickname']


@admin.register(DebitAccount)
class DebitAccountAdmin(admin.ModelAdmin):
    list_display = ['iban', 'client_number', 'card_holder', 'nickname']
    search_fields = ['iban', 'card_holder', 'nickname']


@admin.register(CategoryGroup)
class CategoryGroupAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug']
    readonly_fields = ['name', 'slug']

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CategoryNode)
class CategoryNodeAdmin(admin.ModelAdmin):
    list_display = ['name', 'parent', 'color', 'group', 'user']
    list_filter = ['group']
    search_fields = ['name']


@admin.register(ClassificationRuleV2)
class ClassificationRuleV2Admin(admin.ModelAdmin):
    list_display = ['description', 'category', 'account_type', 'user']
    list_filter = ['category__group']
    search_fields = ['description']


@admin.register(RawTransaction)
class RawTransactionAdmin(admin.ModelAdmin):
    list_display = ['date', 'description', 'amount', 'normalized_amount']
    search_fields = ['description']
    date_hierarchy = 'date'
    list_per_page = 50


@admin.register(LogicalTransaction)
class LogicalTransactionAdmin(admin.ModelAdmin):
    list_display = ['date', 'description', 'amount', 'category_v2', 'classification_method_v2']
    list_filter = ['classification_method_v2', 'category_v2__group', 'date']
    search_fields = ['description']
    raw_id_fields = ['category_v2', 'matched_rule_v2']
    date_hierarchy = 'date'
    list_per_page = 50


class CurrencyLedgerInline(admin.TabularInline):
    model = CurrencyLedger
    extra = 0
    readonly_fields = ['currency', 'previous_balance', 'balance_at_cutoff']


@admin.register(StatementImport)
class StatementImportAdmin(admin.ModelAdmin):
    list_display = ['filename', 'account', 'statement_date', 'points_assigned', 'imported_at']
    list_filter = ['account__account_type', 'account']
    inlines = [CurrencyLedgerInline]


@admin.register(TransactionPair)
class TransactionPairAdmin(admin.ModelAdmin):
    list_display = ['status', 'match_method', 'outgoing', 'incoming', 'created_at']
    list_filter = ['status', 'match_method']
    raw_id_fields = ['outgoing', 'incoming']
