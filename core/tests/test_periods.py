from datetime import date

import pytest

from core.services.periods import build_calendar_periods, build_period_options, months_before


def test_calendar_periods_include_full_boundaries_and_gaps():
    months, quarters, semesters, years = build_calendar_periods(date(2023, 12, 15), date(2024, 7, 2))

    assert [p['key'] for p in months] == [
        '2024-07', '2024-06', '2024-05', '2024-04', '2024-03', '2024-02', '2024-01', '2023-12',
    ]
    assert next(p for p in months if p['key'] == '2024-02')['end'] == date(2024, 2, 29)
    assert [(p['key'], p['start'], p['end']) for p in quarters] == [
        ('2024-Q3', date(2024, 7, 1), date(2024, 9, 30)),
        ('2024-Q2', date(2024, 4, 1), date(2024, 6, 30)),
        ('2024-Q1', date(2024, 1, 1), date(2024, 3, 31)),
        ('2023-Q4', date(2023, 10, 1), date(2023, 12, 31)),
    ]
    assert [(p['key'], p['start'], p['end']) for p in semesters] == [
        ('2024-H2', date(2024, 7, 1), date(2024, 12, 31)),
        ('2024-H1', date(2024, 1, 1), date(2024, 6, 30)),
        ('2023-H2', date(2023, 7, 1), date(2023, 12, 31)),
    ]
    assert [(p['start'], p['end']) for p in years] == [
        (date(2024, 1, 1), date(2024, 12, 31)),
        (date(2023, 1, 1), date(2023, 12, 31)),
    ]


@pytest.mark.parametrize('earliest,latest', [
    (None, None), (date(2024, 1, 1), date(2024, 6, 30)),
])
def test_period_options_keep_rolling_ranges_even_without_history(earliest, latest):
    today = date(2024, 8, 31)
    months, quarters, semesters, years = build_period_options(earliest, latest, today)

    for items, window, expected_start in (
        (quarters, 3, date(2024, 5, 31)),
        (semesters, 6, date(2024, 2, 29)),
        (years, 12, date(2023, 8, 31)),
    ):
        assert items[0] == {
            'key': f'last-{window}-months', 'label': f'Last {window} Months',
            'start': expected_start, 'end': today,
        }
    if earliest is None:
        assert months == []
        assert len(quarters) == len(semesters) == len(years) == 1


def test_months_before_clamps_leap_day():
    assert months_before(date(2024, 2, 29), 12) == date(2023, 2, 28)
