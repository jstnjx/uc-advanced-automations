#!/usr/bin/env bash
# Advanced Automations v2.0.0
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
INTERNAL_PACKAGE="$TMP/bin/_internal/uc_advanced_automations"
for required in \
  driver.json \
  advanced-automations.png \
  THIRD_PARTY_NOTICES.md \
  static/index.html \
  static/styles.css; do
  if [[ ! -e "$INTERNAL_PACKAGE/$required" ]]; then
    echo "ARM64 package is missing runtime data: bin/_internal/uc_advanced_automations/$required" >&2
    exit 1
  fi
done
mkdir -p "$TMP/config"
PORT="${UC_SMOKE_INTEGRATION_PORT:-19001}"
WEB_PORT="${UC_SMOKE_WEB_PORT:-19201}"
LOG="$TMP/driver.log"
START_NS="$(date +%s%N)"

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

# Remote Core aborts setup if a custom integration does not expose its
# Integration API quickly enough. Keep CI below a 4.5 second cold-start budget.
ready=false
for _ in $(seq 1 18); do
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

ELAPSED_MS="$(( ($(date +%s%N) - START_NS) / 1000000 ))"
if [[ "$ready" != true ]]; then
  echo "ARM64 driver did not open 127.0.0.1:$PORT within the 4.5 second Remote startup budget" >&2
  cat "$LOG" >&2
  exit 1
fi

if (( ELAPSED_MS > 4500 )); then
  echo "ARM64 driver exceeded Remote startup budget: ${ELAPSED_MS} ms" >&2
  cat "$LOG" >&2
  exit 1
fi

echo "ARM64 driver opened assigned Integration API socket 127.0.0.1:$PORT in ${ELAPSED_MS} ms"

# A listening TCP socket is not sufficient: the real regression occurred because
# expensive imports began immediately after bind and delayed the first WebSocket
# protocol exchange until Core had already aborted setup. Before a Core protocol
# request arrives the framework/application stack must still be deferred.
sleep 0.2
if grep -q "ucapi-framework lifecycle attached" "$LOG"; then
  echo "Framework loaded before Core activated the bootstrap Integration API session" >&2
  cat "$LOG" >&2
  exit 1
fi

# Minimal RFC 6455 client using only Python's stdlib. Reproduce the first-install
# path: complete the WebSocket upgrade, require ucapi's authentication response,
# send setup_driver, and require its 200 acknowledgement. ucapi acknowledges
# setup_driver before invoking our deferred setup handler, so only after that
# acknowledgement may the expensive framework/application stack begin loading.
python3 - "$PORT" <<'PY'
import base64
import json
import os
import socket
import struct
import sys
import time

port = int(sys.argv[1])
sock = socket.create_connection(("127.0.0.1", port), timeout=2)
key = base64.b64encode(os.urandom(16)).decode("ascii")
request = (
    f"GET / HTTP/1.1\r\n"
    f"Host: 127.0.0.1:{port}\r\n"
    "Upgrade: websocket\r\n"
    "Connection: Upgrade\r\n"
    f"Sec-WebSocket-Key: {key}\r\n"
    "Sec-WebSocket-Version: 13\r\n\r\n"
)
sock.sendall(request.encode("ascii"))
response = b""
while b"\r\n\r\n" not in response:
    chunk = sock.recv(4096)
    if not chunk:
        raise SystemExit("WebSocket handshake closed before HTTP 101 response")
    response += chunk
headers, buffered = response.split(b"\r\n\r\n", 1)
if b" 101 " not in headers.split(b"\r\n", 1)[0]:
    raise SystemExit(f"Unexpected WebSocket handshake response: {headers[:200]!r}")


def read_exact(size: int) -> bytes:
    global buffered
    while len(buffered) < size:
        chunk = sock.recv(4096)
        if not chunk:
            raise SystemExit("WebSocket closed while waiting for server frame")
        buffered += chunk
    data, buffered = buffered[:size], buffered[size:]
    return data


def read_json_frame() -> dict:
    first = read_exact(2)
    opcode = first[0] & 0x0F
    masked = bool(first[1] & 0x80)
    length = first[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", read_exact(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", read_exact(8))[0]
    mask_key = read_exact(4) if masked else None
    payload = read_exact(length)
    if mask_key is not None:
        payload = bytes(byte ^ mask_key[index % 4] for index, byte in enumerate(payload))
    if opcode != 0x1:
        raise SystemExit(f"Expected text frame, got opcode {opcode}")
    return json.loads(payload.decode("utf-8"))


def send_json_frame(message: dict) -> None:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    mask = os.urandom(4)
    length = len(payload)
    if length < 126:
        header = bytes((0x81, 0x80 | length))
    elif length < 65536:
        header = bytes((0x81, 0x80 | 126)) + struct.pack("!H", length)
    else:
        header = bytes((0x81, 0x80 | 127)) + struct.pack("!Q", length)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(header + mask + masked)


auth = read_json_frame()
if auth.get("msg") != "authentication" or auth.get("code") != 200:
    raise SystemExit(f"Unexpected bootstrap authentication response: {auth!r}")

send_json_frame(
    {
        "kind": "req",
        "id": 1,
        "msg": "setup_driver",
        "msg_data": {"reconfigure": False, "setup_data": {}},
    }
)
ack = read_json_frame()
if ack.get("req_id") != 1 or ack.get("msg") != "result" or ack.get("code") != 200:
    raise SystemExit(f"Unexpected setup_driver acknowledgement: {ack!r}")

time.sleep(0.75)
sock.close()
PY

framework_ready=false
for _ in $(seq 1 80); do
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "ARM64 driver exited while loading deferred application stack" >&2
    cat "$LOG" >&2
    exit 1
  fi
  if grep -q "ucapi-framework lifecycle attached after bootstrap activation (setup_driver)" "$LOG"; then
    framework_ready=true
    break
  fi
  sleep 0.25
done

if [[ "$framework_ready" != true ]]; then
  echo "Framework did not attach after authenticated setup_driver was acknowledged" >&2
  cat "$LOG" >&2
  exit 1
fi

if ! grep -q "Core activated bootstrap Integration API via setup_driver" "$LOG"; then
  echo "Bootstrap setup_driver request did not activate deferred startup" >&2
  cat "$LOG" >&2
  exit 1
fi

echo "ARM64 driver authenticated and acknowledged first-install setup before loading ucapi-framework"
cat "$LOG"
