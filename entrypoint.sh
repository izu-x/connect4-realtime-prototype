#!/bin/sh
# Build DATABASE_URL from individual components if not already set.
# CDK injects DB_SECRET_ARN + individual env vars; docker-compose sets DATABASE_URL directly.

if [ -z "$DATABASE_URL" ]; then
    # When deployed via CDK, the secret is injected as JSON in DATABASE_URL_SECRET.
    # Fallback: build from individual env vars passed by CDK stack.
    if [ -n "$DB_HOST" ] && [ -n "$DB_NAME" ]; then
        DATABASE_URL="postgresql+asyncpg://${DB_USERNAME:-connect4}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT:-5432}/${DB_NAME}"
        export DATABASE_URL
    fi
fi

echo "Running Alembic migrations..."
alembic upgrade head

echo "Starting Uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
