#!/bin/bash
set -e

DOMAIN=${1:?"Usage: $0 <domain> <email>"}
EMAIL=${2:?"Usage: $0 <domain> <email>"}
COMPOSE="docker compose -f docker-compose.prod.yml"

echo "=== Step 1: Stop nginx (if running) ==="
$COMPOSE stop nginx 2>/dev/null || true

echo "=== Step 2: Start a temporary HTTP server for ACME challenge ==="
docker run -d --name certbot-http \
  -v projectfinance_certbot-www:/var/www/certbot \
  -p 80:80 \
  nginx:1.27 \
  sh -c 'mkdir -p /var/www/certbot && echo "server { listen 80; location /.well-known/acme-challenge/ { root /var/www/certbot; } location / { return 200 ok; } }" > /etc/nginx/conf.d/default.conf && nginx -g "daemon off;"'

sleep 3

echo "=== Step 3: Request certificate ==="
$COMPOSE run --rm certbot certonly \
  --webroot \
  --webroot-path=/var/www/certbot \
  --email "$EMAIL" \
  --agree-tos \
  --no-eff-email \
  -d "$DOMAIN"

echo "=== Step 4: Create symlink for nginx config ==="
$COMPOSE run --rm certbot \
  sh -c "ln -sfn /etc/letsencrypt/live/$DOMAIN /etc/letsencrypt/live/cert"

echo "=== Step 5: Stop temporary server, start full stack ==="
docker stop certbot-http && docker rm certbot-http
$COMPOSE up -d

echo ""
echo "=== Done! ==="
echo "Your app is live at https://$DOMAIN"