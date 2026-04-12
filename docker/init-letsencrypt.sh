#!/bin/bash
# Obtain initial Let's Encrypt certificates
# Usage: ./docker/init-letsencrypt.sh yourdomain.com your@email.com
#
# Run this once before starting the production stack.

set -e

DOMAIN=${1:?"Usage: $0 <domain> <email>"}
EMAIL=${2:?"Usage: $0 <domain> <email>"}

echo "Obtaining certificate for $DOMAIN..."

# Start nginx temporarily with a self-signed cert
docker compose -f docker-compose.prod.yml up -d nginx

# Request the certificate
docker compose -f docker-compose.prod.yml run --rm certbot certonly \
  --webroot \
  --webroot-path=/var/www/certbot \
  --email "$EMAIL" \
  --agree-tos \
  --no-eff-email \
  -d "$DOMAIN"

# Create a symlink so nginx config works with a generic path
docker compose -f docker-compose.prod.yml exec nginx \
  ln -sf /etc/letsencrypt/live/"$DOMAIN" /etc/letsencrypt/live/cert

echo ""
echo "Certificate obtained! Now start the full stack:"
echo "  docker compose -f docker-compose.prod.yml up -d"
