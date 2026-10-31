#!/usr/bin/env bash
# Production / Replit entry point: apply database migrations, then serve on 0.0.0.0:$PORT.
#
# ONE worker on purpose: live game state (locks, timers, WebSocket rooms) lives in this process.
# --proxy-headers makes uvicorn trust X-Forwarded-Proto / -For from Replit's proxy, so request.url.scheme
# is "https" and cookies get the Secure flag automatically.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "${APP_ENV:-development}" = "production" ] && [ -z "${SECRET_KEY:-}" ]; then
  echo "ERROR: SECRET_KEY is not set. In Replit open Tools -> Secrets and add SECRET_KEY" >&2
  echo "       (generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\")" >&2
  exit 1
fi

python -m alembic upgrade head

exec python -m uvicorn app.main:app \
  --host 0.0.0.0 --port "${PORT:-8000}" \
  --workers 1 \
  --proxy-headers --forwarded-allow-ips='*' \
  --log-level "${LOG_LEVEL:-info}"
