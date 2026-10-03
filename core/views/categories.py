from django.db.models import Count, Q
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST

from ..models import Transaction, Category, CategoryGroup, ClassificationRule

__all__ = [
    'category_suggestions',
]


DEFAULT_CATEGORIES = {
    'expense': [
        ('Bank Fees & Charges', '#c0392b'),
        ('Bank Insurance', '#b71c1c'),
        ('Bank Interest Charges', '#a93226'),
        ('Bank Transactions', '#78909c'),
        ('Car Gas', '#2980b9'),
        ('Car Insurance', '#d35400'),
        ('Car Maintenance', '#5dade2'),
        ('Car Parking & Toll', '#607d8b'),
        ('Car Tax', '#8e44ad'),
        ('Car Wash', '#3498db'),
        ('Family Pet', '#00bcd4'),
        ('Food Delivery', '#ff6b6b'),
        ('Food Eating Out', '#e74c3c'),
        ('Food Groceries', '#27ae60'),
        ('Health Dental', '#6c757d'),
        ('Health Fitness', '#1abc9c'),
        ('Health Labs', '#6c757d'),
        ('Health Medical', '#6c757d'),
        ('Health Pharmacy', '#16a085'),
        ('Health Vision', '#6c757d'),
        ('Health Wellness', '#17becf'),
        ('Housing General', '#6610f2'),
        ('Lifestyle Entertainment', '#ff5722'),
        ('Lifestyle Gifts & Donations', '#e91e63'),
        ('Lifestyle Subscriptions', '#9c27b0'),
        ('Shopping Clothing', '#f39c12'),
        ('Shopping General', '#e67e22'),
        ('Shopping Online', '#ff9800'),
        ('Transport General', '#546e7a'),
        ('Transport Uber', '#3498db'),
        ('Travel Activities', '#6c757d'),
        ('Travel Agency', '#fd7e14'),
        ('Travel Flights', '#6c757d'),
        ('Travel Food', '#6c757d'),
        ('Travel General', '#6c757d'),
        ('Travel Lodging', '#6c757d'),
        ('Utilities Electricity', '#ffc107'),
        ('Utilities General', '#34495e'),
        ('Utilities Internet', '#0097a7'),
        ('Utilities Phone', '#5c6bc0'),
        ('Utilities Water', '#0288d1'),
    ],
    'income': [
        ('Bank Interest CDP', '#66bb6a'),
        ('Bank Interest Cashback', '#43a047'),
        ('Bank Interest Credit', '#6c757d'),
        ('Bank Interest Reversals', '#81c784'),
        ('Reimbursement General', '#80cbc4'),
        ('Reimbursement Housing', '#7e57c2'),
        ('Reimbursement Insurance', '#9575cd'),
        ('Reimbursement Partner', '#6c757d'),
        ('Work Association', '#388e3c'),
        ('Work Bonuses', '#2e7d32'),
        ('Work Government', '#6c757d'),
        ('Work Salary', '#28a745'),
    ],
    'transfer': [
        ('Account BAC CDP', '#8d6e63'),
        ('Account BAC Credit', '#6c757d'),
        ('Account BAC Debit', '#5c6bc0'),
        ('Account COOPENAE', '#6c757d'),
        ('Account MultiMoney', '#6c757d'),
        ('Broker Etoro', '#6c757d'),
        ('Broker Interactive Brokers', '#6c757d'),
        ('External Cash', '#795548'),
    ],
}


@login_required
def category_suggestions(request):
    """Page for loading default category templates."""
    if request.method == 'POST' and 'load_selected' in request.POST:
        selected = request.POST.getlist('selected_cats')
        created = 0
        skipped = 0
        for item in selected:
            group_slug, name = item.split(':', 1)
            group = CategoryGroup.get_group(group_slug)
            color = dict(DEFAULT_CATEGORIES.get(group_slug, [])).get(name, '#6c757d')
            _, was_created = Category.objects.get_or_create(
                name=name, group=group, user=request.user,
                defaults={'color': color},
            )
            if was_created:
                created += 1
            else:
                skipped += 1
        messages.success(request, f'Created {created} categories. {skipped} already existed.')
        return redirect('core:category_suggestions')

    # Build preview data: which categories exist, which are new
    existing = set(
        Category.objects.filter(user=request.user)
        .values_list('group__slug', 'name')
    )

    preview = {}
    for group_slug, cats in DEFAULT_CATEGORIES.items():
        group_preview = []
        for name, color in cats:
            group_preview.append({
                'name': name,
                'color': color,
                'exists': (group_slug, name) in existing,
            })
        preview[group_slug] = group_preview

    new_count = sum(1 for cats in preview.values() for c in cats if not c['exists'])
    existing_count = sum(1 for cats in preview.values() for c in cats if c['exists'])

    return render(request, 'core/category_suggestions.html', {
        'preview': preview,
        'new_count': new_count,
        'existing_count': existing_count,
        'total_count': new_count + existing_count,
    })
