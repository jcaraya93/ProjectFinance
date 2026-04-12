# Grafana Cloud Setup

Free tier includes 10k metrics, 50GB logs, 50GB traces per month.

## 1. Create Account

1. Go to https://grafana.com/auth/sign-up/create-user
2. Sign up with GitHub or email
3. Choose the **Free** plan

## 2. Get OTLP Credentials

1. In Grafana Cloud, go to **Home → Connections → Add new connection**
2. Search for **OpenTelemetry (OTLP)**
3. Click **Configure**
4. Note these values:
   - **OTLP endpoint** (e.g., `https://otlp-gateway-prod-us-east-0.grafana.net/otlp`)
   - **Instance ID** (a number)
   - **API Token** — click **Generate now** to create one

## 3. Build the Authorization Header

The header format is `Basic <base64(instanceId:token)>`.

Generate it:

```bash
echo -n "<instance-id>:<api-token>" | base64
```

Or in PowerShell:

```powershell
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("<instance-id>:<api-token>"))
```

## 4. Configure Your Environment

Add these to your `.env` (local) or `.env.prod` (production):

```
OTEL_EXPORTER=otlp-http
OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp-gateway-prod-us-east-0.grafana.net/otlp
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic <base64-value-from-step-3>
```

## 5. Verify

Restart your app and generate some traffic. In Grafana Cloud:

- **Explore → Logs** — search for `{service_name="project-finance"}`
- **Explore → Traces** — search by service name
- **Explore → Metrics** — query `http_server_duration_milliseconds_count`

Data should appear within 1-2 minutes.

## Exporter Modes

| `.env` value | Protocol | Use case |
|---|---|---|
| `OTEL_EXPORTER=console` | stdout | Local dev, no backend needed |
| `OTEL_EXPORTER=otlp` | gRPC | Local Docker otel-collector |
| `OTEL_EXPORTER=otlp-http` | HTTP | Grafana Cloud or any HTTP OTLP endpoint |
