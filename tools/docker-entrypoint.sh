#!/bin/sh
set -eu

export UC_EXTERNAL="${UC_EXTERNAL:-true}"
export UC_RUNTIME_MODE="${UC_RUNTIME_MODE:-external}"
export UC_DISABLE_MDNS_PUBLISH="${UC_DISABLE_MDNS_PUBLISH:-true}"
export UC_INTEGRATION_INTERFACE="${UC_INTEGRATION_INTERFACE:-0.0.0.0}"
export UC_INTEGRATION_HTTP_PORT="${UC_INTEGRATION_HTTP_PORT:-9090}"
export UC_AUTOMATIONS_WEB_HOST="${UC_AUTOMATIONS_WEB_HOST:-0.0.0.0}"

# The UC External Integration Installer mounts persistent configuration at
# /config and supplies UC_CONFIG_HOME=/config. Standalone Docker deployments
# keep using /data unless an explicit data directory was provided.
if [ -z "${UC_AUTOMATIONS_DATA_DIR:-}" ]; then
  if [ -n "${UC_CONFIG_HOME:-}" ]; then
    export UC_AUTOMATIONS_DATA_DIR="$UC_CONFIG_HOME"
  else
    export UC_AUTOMATIONS_DATA_DIR=/data
  fi
fi

# Keep the documented automation-editor port stable in installer-managed
# deployments. The Python startup allocator scans upward from 9201 when the port is occupied.
export UC_AUTOMATIONS_WEB_PORT="${UC_AUTOMATIONS_WEB_PORT:-9201}"

mkdir -p "$UC_AUTOMATIONS_DATA_DIR"
# Mounted volumes can be created with restrictive ownership by an installer.
# Relax owner permissions when possible, but still fail with a precise error if
# the current container user cannot write configuration files.
chmod u+rwx "$UC_AUTOMATIONS_DATA_DIR" 2>/dev/null || true
if [ ! -w "$UC_AUTOMATIONS_DATA_DIR" ]; then
  echo "fatal: data directory is not writable: $UC_AUTOMATIONS_DATA_DIR" >&2
  echo "container uid=$(id -u) gid=$(id -g)" >&2
  ls -ld "$UC_AUTOMATIONS_DATA_DIR" >&2 || true
  exit 78
fi

printf '%s\n' "$UC_AUTOMATIONS_WEB_PORT" > /tmp/uc-advanced-automations-web-port

echo "Starting UC Advanced Automations: runtime=$UC_RUNTIME_MODE data=$UC_AUTOMATIONS_DATA_DIR web=$UC_AUTOMATIONS_WEB_HOST:$UC_AUTOMATIONS_WEB_PORT integration=$UC_INTEGRATION_INTERFACE:$UC_INTEGRATION_HTTP_PORT mdns_disabled=$UC_DISABLE_MDNS_PUBLISH" >&2

# Preserve the standard Docker command contract so installers may override CMD
# without replacing this environment and data-directory initialization.
if [ "$#" -eq 0 ]; then
  set -- uc-advanced-automations
fi
exec "$@"
