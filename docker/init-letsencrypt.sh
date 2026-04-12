#!/bin/bash
set -e

DOMAIN=${1:?"Usage: $0 <domain> <email>"}
EMAIL=${2:?"Usage: $0 <domain> <email>"}

echo "=== Step 1: Creating temporary HTTP-only nginx config ==="
cat > /tmp/nginx-http-only.conf << 'NGINX'
server {
    listen 80;
    server_name _;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 200 'Waiting for SSL setup...'; }
}
NGINX

# Override nginx config temporarily
docker cp /tmp/nginx-http-only.conf projectfinance-nginx-1:/etc/nginx/conf.d/default.conf
docker exec projectfinance-nginx-1 nginx -s reload 2>/dev/null || docker compose -f docker-compose.prod.yml restart nginx

echo "=== Step 2: Requesting certificate ==="
sleep 3
docker compose -f docker-compose.prod.yml run --rm certbot certonly \
  --webroot \
  --webroot-path=/var/www/certbot \
  --email "$EMAIL" \
  --agree-tos \
  --no-eff-email \
  -d "$DOMAIN"

echo "=== Step 3: Creating symlink ==="
docker compose -f docker-compose.prod.yml run --rm certbot \
  sh -c "ln -sfn /etc/letsencrypt/live/$DOMAIN /etc/letsencrypt/live/cert"

echo "=== Step 4: Restoring full nginx config with HTTPS ==="
docker compose -f docker-compose.prod.yml restart nginx

echo ""
echo "=== Done! ==="
echo "Your app is live at https://$DOMAIN"