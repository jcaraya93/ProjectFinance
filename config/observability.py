"""
OpenTelemetry observability bootstrap for the finance project.

Configures tracing, metrics, and log-export with console exporters (dev)
or OTLP exporters (production / Grafana Cloud).  Call ``init_observability()``
once at process startup - typically from wsgi.py / asgi.py.

Toggle exporter back-end via the OTEL_EXPORTER env-var:
    "console"   - stdout (default, useful for local development)
    "otlp"      - OTLP/gRPC to a local collector (e.g. Docker otel-collector)
    "otlp-http" - OTLP/HTTP to a cloud endpoint (e.g. Grafana Cloud)
"""

import logging
import os

from opentelemetry import trace, metrics, _logs
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, ConsoleLogExporter
from opentelemetry.instrumentation.django import DjangoInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor

logger = logging.getLogger(__name__)

_initialised = False


def _build_resource() -> Resource:
    service_name = os.environ.get("OTEL_SERVICE_NAME", "project-finance")
    return Resource.create({"service.name": service_name})


def _get_exporter_type() -> str:
    return os.environ.get("OTEL_EXPORTER", "console").lower()


def _get_otlp_headers():
    """Parse OTEL_EXPORTER_OTLP_HEADERS env var into a dict."""
    raw = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")
    if not raw:
        return None
    headers = {}
    for pair in raw.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            headers[k.strip()] = v.strip()
    return headers or None


def _setup_tracing(resource: Resource, exporter_type: str) -> None:
    provider = TracerProvider(resource=resource)
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    headers = _get_otlp_headers()

    if exporter_type == "otlp":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers)))
    elif exporter_type == "otlp-http":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", headers=headers)))
    else:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)


def _setup_metrics(resource: Resource, exporter_type: str) -> None:
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    headers = _get_otlp_headers()

    if exporter_type == "otlp":
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint, headers=headers), export_interval_millis=30_000)
    elif exporter_type == "otlp-http":
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", headers=headers), export_interval_millis=30_000)
    else:
        reader = PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=30_000)
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)


def _setup_logging(resource: Resource, exporter_type: str) -> None:
    log_provider = LoggerProvider(resource=resource)
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    headers = _get_otlp_headers()

    if exporter_type == "otlp":
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
        log_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint, headers=headers)))
    elif exporter_type == "otlp-http":
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        log_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{endpoint}/v1/logs", headers=headers)))
    else:
        log_provider.add_log_record_processor(BatchLogRecordProcessor(ConsoleLogExporter()))

    _logs.set_logger_provider(log_provider)


def init_observability() -> None:
    """Initialise OpenTelemetry tracing, metrics, and logging.

    Safe to call more than once - subsequent calls are no-ops.
    """
    global _initialised
    if _initialised:
        return
    _initialised = True

    resource = _build_resource()
    exporter_type = _get_exporter_type()

    _setup_tracing(resource, exporter_type)
    _setup_metrics(resource, exporter_type)
    _setup_logging(resource, exporter_type)

    # Instrument Django (views, middleware, template rendering).
    DjangoInstrumentor().instrument()

    # Inject otelTraceID / otelSpanID / otelServiceName into every
    # stdlib logging record so the LOGGING formatter can include them.
    LoggingInstrumentor().instrument(set_logging_format=True)

    logger.info("OpenTelemetry observability initialised (exporter=%s)", exporter_type)