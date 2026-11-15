#!/usr/bin/env bash
# Throwaway local stack for recording: local mongod, local ML service, Better Auth, Flask.
# Nothing here touches Atlas or the hosted ML host. Run from Git Bash: bash demo_video/stack/start.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN="$ROOT/demo_video/stack/run"
mkdir -p "$RUN/db" "$RUN/ml" "$RUN/logs"
[ -f "$RUN/ml_token" ] || python -c "import secrets; print(secrets.token_urlsafe(40))" > "$RUN/ml_token"
[ -f "$RUN/auth_secret" ] || python -c "import secrets; print(secrets.token_urlsafe(40))" > "$RUN/auth_secret"

MONGOD="$(ls -d "/c/Program Files/MongoDB/Server"/*/bin | tail -1)/mongod.exe"
export MONGODB_URI="mongodb://127.0.0.1:27027"
export MONGODB_DB="inquisitor_demo"
export ML_TOKEN_FILE="$RUN/ml_token"
export ML_SERVICE_URL="http://127.0.0.1:18765"
export BETTER_AUTH_URL="http://127.0.0.1:5000"
export BETTER_AUTH_SECRET="$(cat "$RUN/auth_secret")"
# Only the Gemini key is read from the repo .env; it gets aggregate alert context (see docs/privacy.md).
GEMINI_API_KEY="$(grep -m1 '^GEMINI_API_KEY=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- | tr -d '\r"' || true)"
if [ -n "$GEMINI_API_KEY" ]; then export GEMINI_API_KEY; else unset GEMINI_API_KEY; fi

"$MONGOD" --dbpath "$RUN/db" --port 27027 --bind_ip 127.0.0.1 --replSet rs0 > "$RUN/logs/mongod.log" 2>&1 &
sleep 3
mongosh --quiet --port 27027 --eval "try { rs.status() } catch (e) { rs.initiate({_id: \"rs0\", members: [{_id: 0, host: \"127.0.0.1:27027\"}]}) }" > /dev/null; sleep 3

(cd "$ROOT" && ML_ALLOW_TRAINING=1 ML_DATA_DIR="$RUN/ml" MONGODB_DB=inquisitor_demo_ml \
  uv run --project backend python -m flask --app backend.ml.app:create_app run --port 18765 \
  > "$RUN/logs/ml.log" 2>&1) &

(cd "$ROOT/Frontend" && node --import "file:///$(cygpath -m "$ROOT")/demo_video/stack/remap-port.mjs" --experimental-strip-types auth/server.mts > "$RUN/logs/auth.log" 2>&1) &

(cd "$ROOT/Frontend" && npm run build > "$RUN/logs/build.log" 2>&1 && \
  INQUISITOR_ARTIFACT_DIR="$RUN/artifacts" PORT=5000 \
  uv run --no-project --with-requirements requirements.txt python "$ROOT/demo_video/stack/flask_server.py" > "$RUN/logs/flask.log" 2>&1) &

wait
