@echo off
REM Start SigNoz for local development.
REM Prerequisites: Docker Desktop must be running.
REM
REM SigNoz UI:  http://localhost:8080
REM OTLP gRPC:  localhost:4317
REM OTLP HTTP:  localhost:4318

setlocal

set SIGNOZ_DIR=%~dp0signoz

if not exist "%SIGNOZ_DIR%" (
    echo Cloning SigNoz repository...
    git clone --depth 1 -b main https://github.com/SigNoz/signoz.git "%SIGNOZ_DIR%"
)

cd /d "%SIGNOZ_DIR%\deploy\docker"
echo Starting SigNoz...
docker compose up -d

echo.
echo SigNoz is starting up. It may take a minute on first run.
echo.
echo   Dashboard:  http://localhost:8080
echo   OTLP gRPC:  localhost:4317
echo   OTLP HTTP:  localhost:4318
echo.
echo To send telemetry from Django, set these in your .env:
echo   OTEL_EXPORTER=otlp
echo   OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
echo.
echo To stop: docker compose -f "%SIGNOZ_DIR%\deploy\docker\docker-compose.yaml" down

endlocal
