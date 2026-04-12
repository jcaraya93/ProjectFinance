#!/usr/bin/env bash
# Start SigNoz for local development.
# Prerequisites: Docker must be running.
#
# SigNoz UI:  http://localhost:8080
# OTLP gRPC:  localhost:4317
# OTLP HTTP:  localhost:4318

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SIGNOZ_DIR="$SCRIPT_DIR/signoz"

if [ ! -d "$SIGNOZ_DIR" ]; then
    echo "Cloning SigNoz repository..."
    git clone --depth 1 -b main https://github.com/SigNoz/signoz.git "$SIGNOZ_DIR"
fi

cd "$SIGNOZ_DIR/deploy/docker"
echo "Starting SigNoz..."
docker compose up -d

echo ""
echo "SigNoz is starting up. It may take a minute on first run."
echo ""
echo "  Dashboard:  http://localhost:8080"
echo "  OTLP gRPC:  localhost:4317"
echo "  OTLP HTTP:  localhost:4318"
echo ""
echo "To send telemetry from Django, set these in your .env:"
echo "  OTEL_EXPORTER=otlp"
echo "  OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317"
echo ""
echo "To stop: docker compose -f $SIGNOZ_DIR/deploy/docker/docker-compose.yaml down"
