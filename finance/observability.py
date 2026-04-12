"""
OpenTelemetry observability bootstrap for the finance project.

Configures tracing, metrics, and log-export with console exporters (dev)
or OTLP exporters (production).  Call ``init_observability()`` once at
process startup — typically from wsgi.py / asgi.py.

Toggle exporter back-end via the OTEL_EXPORTER env-var:
    "console"  → stdout (default, useful for local development)
    "otlp"     → OTLP/gRPC (requires opentelemetry-exporter-otlp)
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


def _setup_tracing(resource: Resource, exporter_type: str) -> None:
    provider = TracerProvider(resource=resource)
    if exporter_type == "otlp":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    else:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)


def _setup_metrics(resource: Resource, exporter_type: str) -> None:
    if exporter_type == "otlp":
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint), export_interval_millis=30_000)
    else:
        reader = PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=30_000)
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)


def _setup_logging(resource: Resource, exporter_type: str) -> None:
    log_provider = LoggerProvider(resource=resource)
    if exporter_type == "otlp":
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        log_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint)))
    else:
        log_provider.add_log_record_processor(BatchLogRecordProcessor(ConsoleLogExporter()))

    # Register globally so the LoggingHandler created by Django's LOGGING
    # dictConfig (which cannot receive constructor args) picks it up via
    # get_logger_provider().
    _logs.set_logger_provider(log_provider)


def init_observability() -> None:
    """Initialise OpenTelemetry tracing, metrics, and logging.

    Safe to call more than once — subsequent calls are no-ops.
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
