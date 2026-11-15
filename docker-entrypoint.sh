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

# Optional reverse-tunnel endpoint for the private ML service. The model host connects out to this
# loopback sshd over the /api/ml-tunnel WebSocket and forwards 127.0.0.1:18765 here to its local ML service. The key can only open that forward.
if [ -n "${ML_TUNNEL_HOST_KEY:-}" ] && [ -n "${ML_TUNNEL_AUTHORIZED_KEY:-}" ]; then
    mkdir -p /tmp/sshd && chmod 700 /tmp/sshd
    # Base64 of the private host key file, so the multi-line key survives secret storage intact.
    printf '%s' "$ML_TUNNEL_HOST_KEY" | base64 -d > /tmp/sshd/host_key && chmod 600 /tmp/sshd/host_key
    printf 'restrict,port-forwarding,permitlisten="127.0.0.1:18765",command="/bin/false" %s\n' \
        "$ML_TUNNEL_AUTHORIZED_KEY" > /tmp/sshd/authorized_keys
    cat > /tmp/sshd/sshd_config <<EOF
Port 2222
ListenAddress 127.0.0.1
HostKey /tmp/sshd/host_key
PidFile /tmp/sshd/sshd.pid
AuthorizedKeysFile /tmp/sshd/authorized_keys
AllowUsers $(id -un)
StrictModes no
UsePAM no
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AllowTcpForwarding remote
PermitListen 127.0.0.1:18765
GatewayPorts no
AllowAgentForwarding no
AllowStreamLocalForwarding no
X11Forwarding no
PermitTTY no
PermitTunnel no
ClientAliveInterval 15
ClientAliveCountMax 2
EOF
    echo "Starting ML tunnel endpoint on 127.0.0.1:2222..."
    (while true; do
        /usr/sbin/sshd -D -e -f /tmp/sshd/sshd_config || true
        echo "ML tunnel endpoint exited; restarting in 5s"
        sleep 5
    done) &
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

# gunicorn skips server.py's __main__ block, so create the arcade tables here.
python -c "import server; server.initialize()"

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
