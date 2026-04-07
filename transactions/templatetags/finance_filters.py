from django import template

register = template.Library()


@register.filter
def abs_value(value):
    try:
        return abs(value)
    except (TypeError, ValueError):
        return value


@register.filter
def format_number(value, decimals=0):
    """Format a number with thousand separators (spaces)."""
    try:
        value = float(value)
        if decimals == 0:
            formatted = f"{abs(value):,.0f}"
        else:
            formatted = f"{abs(value):,.{int(decimals)}f}"
        # Replace commas with spaces for thousand separator
        formatted = formatted.replace(',', ' ')
        if value < 0:
            formatted = '-' + formatted
        return formatted
    except (TypeError, ValueError):
        return value


@register.filter
def dict_get(d, key):
    """Get a value from a dict by key (for use in templates)."""
    if isinstance(d, dict):
        return d.get(key, '')
    return ''
