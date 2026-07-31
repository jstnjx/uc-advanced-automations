#!/usr/bin/env bash
set -euo pipefail

ARCHIVE="${1:?Usage: smoke_remote_runtime.sh ARCHIVE.tar.gz}"
[[ "$(uname -m)" == "aarch64" || "$(uname -m)" == "arm64" ]] || {
  echo "Remote runtime smoke test requires an ARM64 host" >&2
  exit 2
}

TMP="$(mktemp -d)"
PID=""
cleanup() {
  if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

tar -xzf "$ARCHIVE" -C "$TMP"
mkdir -p "$TMP/config"
PORT="${UC_SMOKE_INTEGRATION_PORT:-19001}"
WEB_PORT="${UC_SMOKE_WEB_PORT:-19201}"
LOG="$TMP/driver.log"

UC_RUNTIME_MODE=remote \
UC_CONFIG_HOME="$TMP/config" \
UC_AUTOMATIONS_DATA_DIR="$TMP/config" \
UC_INTEGRATION_INTERFACE=127.0.0.1 \
UC_INTEGRATION_HTTP_PORT="$PORT" \
UC_DISABLE_MDNS_PUBLISH=true \
UC_AUTOMATIONS_WEB_HOST=127.0.0.1 \
UC_AUTOMATIONS_WEB_PORT="$WEB_PORT" \
  "$TMP/bin/driver" >"$LOG" 2>&1 &
PID=$!

ready=false
for _ in $(seq 1 40); do
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "ARM64 driver exited before opening the Integration API socket" >&2
    cat "$LOG" >&2
    exit 1
  fi
  if (echo >"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
    ready=true
    break
  fi
  sleep 0.25
done

if [[ "$ready" != true ]]; then
  echo "ARM64 driver did not open 127.0.0.1:$PORT within 10 seconds" >&2
  cat "$LOG" >&2
  exit 1
fi

echo "ARM64 driver opened assigned Integration API socket 127.0.0.1:$PORT"
cat "$LOG"
