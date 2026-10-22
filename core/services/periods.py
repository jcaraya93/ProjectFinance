"""Shared calendar and rolling date ranges for dashboard and transaction filters."""
from calendar import monthrange
from datetime import date


def build_calendar_periods(earliest, latest):
    """Build newest-first calendar periods spanning the supplied dates."""
    months, quarters, semesters, years = [], [], [], []
    if not earliest or not latest:
        return months, quarters, semesters, years

    def month_end(value):
        return value.replace(day=monthrange(value.year, value.month)[1])

    y, m = latest.year, latest.month
    earliest_first = earliest.replace(day=1)
    while date(y, m, 1) >= earliest_first:
        start = date(y, m, 1)
        months.append({'key': f'{y:04d}-{m:02d}', 'label': start.strftime('%b %Y'),
                       'start': start, 'end': month_end(start)})
        m -= 1
        if m < 1:
            m, y = 12, y - 1

    y, q = latest.year, (latest.month - 1) // 3 + 1
    ey, eq = earliest.year, (earliest.month - 1) // 3 + 1
    while (y, q) >= (ey, eq):
        start_month = (q - 1) * 3 + 1
        start = date(y, start_month, 1)
        end = month_end(date(y, start_month + 2, 1))
        quarters.append({'key': f'{y:04d}-Q{q}', 'label': f'Q{q} {y}', 'start': start, 'end': end})
        q -= 1
        if q < 1:
            q, y = 4, y - 1

    y, h = latest.year, 1 if latest.month <= 6 else 2
    ey, eh = earliest.year, 1 if earliest.month <= 6 else 2
    while (y, h) >= (ey, eh):
        start_month = 1 if h == 1 else 7
        start = date(y, start_month, 1)
        end = month_end(date(y, start_month + 5, 1))
        semesters.append({'key': f'{y:04d}-H{h}', 'label': f'H{h} {y}', 'start': start, 'end': end})
        h -= 1
        if h < 1:
            h, y = 2, y - 1

    for y in range(latest.year, earliest.year - 1, -1):
        years.append({'key': f'{y:04d}', 'label': str(y), 'start': date(y, 1, 1), 'end': date(y, 12, 31)})

    return months, quarters, semesters, years


def months_before(value, months):
    total_months = (value.year * 12 + value.month - 1) - months
    year = total_months // 12
    month = total_months % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def build_period_options(earliest, latest, today):
    months, quarters, semesters, years = build_calendar_periods(earliest, latest)
    for items, window in ((quarters, 3), (semesters, 6), (years, 12)):
        items.insert(0, {
            'key': f'last-{window}-months',
            'label': f'Last {window} Months',
            'start': months_before(today, window),
            'end': today,
        })
    return months, quarters, semesters, years
