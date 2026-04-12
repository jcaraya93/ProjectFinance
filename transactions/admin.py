from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, Account, CreditAccount, DebitAccount, Category, CategoryGroup, CurrencyLedger, StatementImport, RawTransaction, LogicalTransaction, Transaction


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


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'color', 'group']
    list_filter = ['group']
    search_fields = ['name']


@admin.register(RawTransaction)
class RawTransactionAdmin(admin.ModelAdmin):
    list_display = ['date', 'description', 'amount']
    search_fields = ['description']
    date_hierarchy = 'date'
    list_per_page = 50


@admin.register(LogicalTransaction)
class LogicalTransactionAdmin(admin.ModelAdmin):
    list_display = ['date', 'description', 'amount', 'category', 'classification_method']
    list_filter = ['classification_method', 'category__group', 'category', 'date']
    search_fields = ['description']
    list_editable = ['category']
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
