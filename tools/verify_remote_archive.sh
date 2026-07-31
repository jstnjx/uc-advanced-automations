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

python - "$TMP" <<'PY'
import json
import pathlib
import struct
import sys

archive_root = pathlib.Path(sys.argv[1])
metadata = json.loads((archive_root / "driver.json").read_text(encoding="utf-8"))
version = (archive_root / "version.txt").read_text(encoding="utf-8").strip().removeprefix("v")
if metadata.get("version") != version:
    raise SystemExit(
        f"driver.json version {metadata.get('version')} does not match version.txt {version}"
    )

icon_reference = metadata.get("icon")
if not isinstance(icon_reference, str) or not icon_reference.startswith("custom:"):
    raise SystemExit("driver.json icon must use a custom:<filename> reference")
icon_path = archive_root / icon_reference.removeprefix("custom:")
if not icon_path.is_file():
    raise SystemExit(f"Remote archive is missing metadata icon: {icon_path.name}")

header = icon_path.read_bytes()[:24]
if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
    raise SystemExit(f"Metadata icon is not a readable PNG: {icon_path.name}")
width, height = struct.unpack(">II", header[16:24])
if (width, height) != (90, 90):
    raise SystemExit(
        f"Invalid metadata icon size: expected 90x90 but got {width}x{height} ({icon_path.name})"
    )
PY

BINARY_INFO="$(file "$TMP/bin/driver")"
echo "$BINARY_INFO"
case "$BINARY_INFO" in
  *ELF*64-bit*ARM*aarch64*|*ELF*64-bit*ARM64*|*ELF*64-bit*AArch64*) ;;
  *) echo "bin/driver is not an ARM64 ELF executable" >&2; exit 1 ;;
esac

echo "Verified Remote ARM64 custom-install archive: $ARCHIVE"
