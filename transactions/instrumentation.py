"""
Centralized OpenTelemetry instrumentation for the transactions app.

Provides a shared tracer, meter, and pre-defined metrics so every module
uses consistent naming.  Import what you need::

    from transactions.instrumentation import tracer, meter, ...
"""

from opentelemetry import trace, metrics

tracer = trace.get_tracer("transactions")
meter = metrics.get_meter("transactions")

# ── Counters ──────────────────────────────────────────────────

transactions_imported = meter.create_counter(
    "transactions.imported",
    unit="{transaction}",
    description="Total transactions imported from CSV files",
)

classification_result = meter.create_counter(
    "classification.result",
    unit="{transaction}",
    description="Classification outcomes by method (rule, manual, unclassified, ai)",
)

parser_files_processed = meter.create_counter(
    "parser.files_processed",
    unit="{file}",
    description="CSV files processed by parser type and status",
)

exchange_rate_fetches = meter.create_counter(
    "exchange_rate.fetches",
    unit="{request}",
    description="Exchange rate API calls by outcome",
)

ai_classifier_calls = meter.create_counter(
    "ai_classifier.calls",
    unit="{request}",
    description="AI classifier API calls by outcome",
)

# ── Histograms ────────────────────────────────────────────────

dashboard_duration = meter.create_histogram(
    "dashboard.render_duration",
    unit="ms",
    description="Dashboard rendering time by dashboard type",
)

classification_duration = meter.create_histogram(
    "classification.duration",
    unit="ms",
    description="Time spent classifying transactions",
)

parser_duration = meter.create_histogram(
    "parser.duration",
    unit="ms",
    description="CSV parsing time by parser type",
)

exchange_rate_api_duration = meter.create_histogram(
    "exchange_rate.api_duration",
    unit="ms",
    description="Frankfurter API response time",
)

upload_duration = meter.create_histogram(
    "upload.duration",
    unit="ms",
    description="Total statement upload processing time",
)

bulk_import_duration = meter.create_histogram(
    "bulk_import.duration",
    unit="ms",
    description="Bulk import command total duration",
)
