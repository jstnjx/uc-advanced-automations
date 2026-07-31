#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ARCH="${1:-aarch64}"
HOST_ARCH="$(uname -m)"
case "$HOST_ARCH" in
  aarch64|arm64) ;;
  *)
    echo "Remote packages must be built in an ARM64 runtime. Current runtime: $HOST_ARCH" >&2
    echo "Use the included native ARM64 GitHub Actions workflow or run the official r2-pyinstaller image on an ARM64 host." >&2
    exit 2
    ;;
esac
[[ "$ARCH" == "aarch64" || "$ARCH" == "arm64" ]] || { echo "Unsupported Remote architecture: $ARCH" >&2; exit 2; }
ARCH="aarch64"

VERSION="$(python -c 'import json; print(json.load(open("driver.json"))["version"])')"
NAME="uc-intg-advanced-automations-v${VERSION}-${ARCH}"

# Fail before the expensive PyInstaller build if the Remote metadata icon is invalid.
python - <<'PY'
import json
import pathlib
import struct

root = pathlib.Path(".")
metadata = json.loads((root / "driver.json").read_text(encoding="utf-8"))
icon_reference = metadata.get("icon")
if not isinstance(icon_reference, str) or not icon_reference.startswith("custom:"):
    raise SystemExit("driver.json icon must use a custom:<filename> reference")
icon_path = root / icon_reference.removeprefix("custom:")
if not icon_path.is_file():
    raise SystemExit(f"Metadata icon does not exist: {icon_path}")
header = icon_path.read_bytes()[:24]
if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
    raise SystemExit(f"Metadata icon is not a readable PNG: {icon_path}")
width, height = struct.unpack(">II", header[16:24])
if (width, height) != (90, 90):
    raise SystemExit(
        f"Remote metadata icon must be exactly 90x90; got {width}x{height}: {icon_path}"
    )
PY

rm -rf build dist artifacts driver.spec
python -m PyInstaller \
  --clean \
  --onedir \
  --name driver \
  --paths src \
  --collect-data uc_advanced_automations \
  driver.py

mkdir -p artifacts/bin
cp -R dist/driver/. artifacts/bin/
cp driver.json artifacts/
cp advanced-automations.png artifacts/
printf 'v%s\n' "$VERSION" > artifacts/version.txt

tar -czf "${NAME}.tar.gz" -C artifacts .
printf '%s\n' "${ROOT_DIR}/${NAME}.tar.gz"

# Verify the exact archive before returning it to the caller.
bash ./tools/verify_remote_archive.sh "${NAME}.tar.gz"
