from django.db.models import Prefetch
from django.utils.http import url_has_allowed_host_and_scheme

from ..models import Category, CategoryGroup


def _safe_next_url(request, default=''):
    """Return the 'next' param only if it points to this site."""
    next_url = request.GET.get('next', request.POST.get('next', default))
    if next_url and not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return default
    return next_url


def get_category_groups(user, exclude_unclassified=False):
    """CategoryGroups with prefetched user-scoped categories."""
    qs = CategoryGroup.objects.prefetch_related(
        Prefetch('categories', queryset=Category.objects.filter(user=user))
    )
    if exclude_unclassified:
        qs = qs.exclude(slug='unclassified')
    return qs.order_by('name')
