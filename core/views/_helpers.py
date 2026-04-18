from django.utils.http import url_has_allowed_host_and_scheme


def _safe_next_url(request, default=''):
    """Return the 'next' param only if it points to this site."""
    next_url = request.GET.get('next', request.POST.get('next', default))
    if next_url and not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return default
    return next_url
