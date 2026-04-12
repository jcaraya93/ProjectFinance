#!/bin/sh
set -e

echo "Waiting for database..."
python -c "
import time, os, psycopg
for i in range(30):
    try:
        psycopg.connect(
            dbname=os.environ.get('POSTGRES_DB', 'projectfinance'),
            user=os.environ.get('POSTGRES_USER', 'projectfinance'),
            password=os.environ.get('POSTGRES_PASSWORD', 'projectfinance'),
            host=os.environ.get('POSTGRES_HOST', 'db'),
            port=os.environ.get('POSTGRES_PORT', '5432'),
        ).close()
        print('Database is ready!')
        break
    except Exception:
        print(f'Waiting... ({i+1}/30)')
        time.sleep(2)
else:
    print('Database not available after 60s')
    exit(1)
"

echo "Running migrations..."
python manage.py migrate --noinput

echo "Collecting static files..."
python manage.py collectstatic --noinput

exec "$@"
