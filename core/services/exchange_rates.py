import json
import time
from datetime import date, timedelta
from decimal import Decimal
from urllib.request import urlopen
from urllib.error import URLError

from core.models import ExchangeRate
from core.instrumentation import tracer, exchange_rate_fetches, exchange_rate_api_duration


FRANKFURTER_URL = 'https://api.frankfurter.dev/v2/rates'


def fetch_rates(start_date, end_date):
    """Fetch USD→CRC exchange rates from Frankfurter API and cache them."""
    with tracer.start_as_current_span("exchange_rates.fetch") as span:
        span.set_attribute("rates.start_date", str(start_date))
        span.set_attribute("rates.end_date", str(end_date))

        existing = set(
            ExchangeRate.objects.filter(
                date__gte=start_date, date__lte=end_date
            ).values_list('date', flat=True)
        )

        all_dates = set()
        d = start_date
        while d <= end_date:
            all_dates.add(d)
            d += timedelta(days=1)

        missing = all_dates - existing
        span.set_attribute("rates.missing_count", len(missing))

        if not missing:
            span.set_attribute("rates.cache_hit", True)
            return

        url = f'{FRANKFURTER_URL}?from={start_date}&to={end_date}&base=USD&quotes=CRC'
        t0 = time.monotonic()
        try:
            from urllib.request import Request
            req = Request(url, headers={'User-Agent': 'ProjectFinance/1.0'})
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except (URLError, json.JSONDecodeError) as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            exchange_rate_api_duration.record(elapsed_ms)
            exchange_rate_fetches.add(1, {"outcome": "error"})
            span.set_attribute("rates.error", str(e))
            raise RuntimeError(f'Failed to fetch exchange rates: {e}')

        elapsed_ms = (time.monotonic() - t0) * 1000
        exchange_rate_api_duration.record(elapsed_ms)
        exchange_rate_fetches.add(1, {"outcome": "success"})
        span.set_attribute("rates.api_duration_ms", elapsed_ms)

        cached_count = 0
        for entry in data:
            rate_date = date.fromisoformat(entry['date'])
            if rate_date not in existing:
                ExchangeRate.objects.update_or_create(
                    date=rate_date,
                    defaults={'usd_to_crc': Decimal(str(entry['rate']))}
                )
                cached_count += 1
        span.set_attribute("rates.cached_count", cached_count)


def get_rate(txn_date):
    """Get the USD→CRC rate for a specific date. Falls back to nearest available."""
    rate = ExchangeRate.objects.filter(date=txn_date).first()
    if rate:
        return rate.usd_to_crc

    # Fallback: nearest previous date
    rate = ExchangeRate.objects.filter(date__lte=txn_date).order_by('-date').first()
    if rate:
        return rate.usd_to_crc

    # Fallback: nearest future date
    rate = ExchangeRate.objects.filter(date__gte=txn_date).order_by('date').first()
    if rate:
        return rate.usd_to_crc

    return None


def convert_transaction(transaction):
    """Set amount_crc and amount_usd on a transaction using exchange rates."""
    currency = transaction.ledger.currency
    rate = get_rate(transaction.date)

    if rate is None:
        return False

    if currency == 'CRC':
        transaction.amount_crc = transaction.amount
        transaction.amount_usd = transaction.amount / rate
    elif currency == 'USD':
        transaction.amount_usd = transaction.amount
        transaction.amount_crc = transaction.amount * rate

    return True


def convert_all_transactions():
    """Convert all transactions that don't have converted amounts."""
    from core.models import Transaction

    unconverted = Transaction.objects.filter(amount_crc__isnull=True) | Transaction.objects.filter(amount_usd__isnull=True)
    count = 0
    for txn in unconverted:
        if convert_transaction(txn):
            txn.save(update_fields=['amount_crc', 'amount_usd'])
            count += 1
    return count
