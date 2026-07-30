#!/usr/bin/env bash
set -euo pipefail

ARCHIVE="${1:?Usage: verify_remote_archive.sh ARCHIVE.tar.gz}"
[[ -f "$ARCHIVE" ]] || { echo "Archive not found: $ARCHIVE" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

tar -xzf "$ARCHIVE" -C "$TMP"
for required in driver.json version.txt advanced-automations.png bin/driver; do
  [[ -e "$TMP/$required" ]] || { echo "Remote archive is missing $required" >&2; exit 1; }
done

python - "$TMP/driver.json" "$TMP/version.txt" <<'PY'
import json, pathlib, sys
metadata = json.loads(pathlib.Path(sys.argv[1]).read_text())
version = pathlib.Path(sys.argv[2]).read_text().strip().removeprefix("v")
if metadata.get("version") != version:
    raise SystemExit(f"driver.json version {metadata.get('version')} does not match version.txt {version}")
PY

BINARY_INFO="$(file "$TMP/bin/driver")"
echo "$BINARY_INFO"
case "$BINARY_INFO" in
  *ELF*64-bit*ARM*aarch64*|*ELF*64-bit*ARM64*|*ELF*64-bit*AArch64*) ;;
  *) echo "bin/driver is not an ARM64 ELF executable" >&2; exit 1 ;;
esac

echo "Verified Remote ARM64 custom-install archive: $ARCHIVE"
