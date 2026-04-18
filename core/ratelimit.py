"""Simple rate limiter using Django's cache framework."""
import functools

from django.core.cache import cache
from django.http import HttpResponse


def ratelimit(key, rate, method='POST'):
    """Decorator that limits requests per IP.

    Args:
        key: A namespace string for the cache key.
        rate: A string like '5/m', '10/h', '100/d'.
        method: HTTP method to limit ('POST', 'GET', 'ALL').
    """
    count_limit, period_code = rate.split('/')
    count_limit = int(count_limit)
    period_map = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
    period = period_map.get(period_code, 60)

    def decorator(view_func):
        @functools.wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if method != 'ALL' and request.method != method:
                return view_func(request, *args, **kwargs)

            forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
            ip = forwarded.split(',')[0].strip() if forwarded else ''
            if not ip:
                ip = request.META.get('REMOTE_ADDR', '127.0.0.1')

            cache_key = f'ratelimit:{key}:{ip}'
            current = cache.get(cache_key, 0)
            if current >= count_limit:
                return HttpResponse(
                    'Rate limit exceeded. Please try again later.', status=429,
                )
            cache.set(cache_key, current + 1, period)
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator
