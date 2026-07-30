#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ARCH="${1:-aarch64}"
VERSION="$(python -c 'import json; print(json.load(open("driver.json"))["version"])')"
NAME="uc-intg-advanced-automations-v${VERSION}-${ARCH}"

rm -rf build dist artifacts
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
