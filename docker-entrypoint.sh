#!/bin/bash
set -e

# Handle ML token if provided as an environment variable
if [ -z "${ML_TOKEN_FILE:-}" ] && [ -n "${ML_TOKEN:-}" ]; then
    echo "$ML_TOKEN" > /tmp/ml_token
    chmod 600 /tmp/ml_token
    export ML_TOKEN_FILE=/tmp/ml_token
elif [ -z "${ML_TOKEN_FILE:-}" ] && [ -n "${ML_SERVICE_TOKEN:-}" ]; then
    echo "$ML_SERVICE_TOKEN" > /tmp/ml_token
    chmod 600 /tmp/ml_token
    export ML_TOKEN_FILE=/tmp/ml_token
fi

# Cleanup handler for graceful shutdown
cleanup() {
    echo "Stopping background services..."
    kill -TERM "$AUTH_PID" "$FLASK_PID" 2>/dev/null || true
    wait "$AUTH_PID" 2>/dev/null || true
    wait "$FLASK_PID" 2>/dev/null || true
    exit 0
}

trap cleanup SIGTERM SIGINT

cd /app/Frontend

# 1. Start Better Auth Node service on loopback (127.0.0.1:3001)
echo "Starting Better Auth service on 127.0.0.1:3001..."
node --experimental-strip-types auth/server.mts &
AUTH_PID=$!

# 2. Start Flask with Gunicorn using gthread workers for WebSocket support
PORT="${PORT:-5000}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-1}"
GUNICORN_THREADS="${GUNICORN_THREADS:-16}"
GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-0}"

echo "Starting Flask web service on 0.0.0.0:${PORT}..."
gunicorn \
    --bind "0.0.0.0:${PORT}" \
    --workers "${GUNICORN_WORKERS}" \
    --threads "${GUNICORN_THREADS}" \
    --timeout "${GUNICORN_TIMEOUT}" \
    server:app &
FLASK_PID=$!

# Wait for either process to terminate
wait -n "$AUTH_PID" "$FLASK_PID"
EXIT_CODE=$?

echo "Process exited with status ${EXIT_CODE}. Shutting down..."
kill -TERM "$AUTH_PID" "$FLASK_PID" 2>/dev/null || true
wait "$AUTH_PID" 2>/dev/null || true
wait "$FLASK_PID" 2>/dev/null || true

exit "$EXIT_CODE"
