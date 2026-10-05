from urllib.parse import urlsplit, urlunsplit

from django.http import QueryDict
from django.utils.http import url_has_allowed_host_and_scheme


def _safe_next_url(request, default=''):
    """Return the 'next' param only if it points to this site."""
    next_url = request.GET.get('next', request.POST.get('next', default))
    if next_url and not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return default
    return next_url


def _bulk_selection_url(request, destination):
    """Keep bulk selections in the transaction-list URL after an action redirect."""
    if not destination:
        return destination

    parsed = urlsplit(destination)
    params = QueryDict(parsed.query, mutable=True)
    if request.POST.get('select_all_matching') == '1':
        params.pop('selected_txn', None)
        params['selected_all'] = '1'
    else:
        selected_ids = list(dict.fromkeys(
            txn_id for txn_id in request.POST.getlist('txn_ids') if txn_id.isdigit()
        ))
        params.pop('selected_all', None)
        if selected_ids:
            params.setlist('selected_txn', selected_ids)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, params.urlencode(), parsed.fragment))
