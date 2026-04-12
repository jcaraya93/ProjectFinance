"""Custom logging formatter that provides safe defaults for OTel fields.

Log records emitted before ``LoggingInstrumentor`` has been initialised
(e.g. Django's autoreload logger) won't have otelTraceID / otelSpanID /
otelServiceName attributes.  This formatter injects sensible defaults so
the ``%(otelXxx)s`` placeholders never raise ``KeyError``.
"""

import logging

_OTEL_DEFAULTS = {
    "otelTraceID": "0",
    "otelSpanID": "0",
    "otelServiceName": "",
}


class OTelFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        for key, default in _OTEL_DEFAULTS.items():
            if not hasattr(record, key):
                setattr(record, key, default)
        return super().format(record)
