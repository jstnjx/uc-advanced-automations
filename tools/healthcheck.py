#!/usr/bin/env python3
"""Container health probe using the runtime-selected web-interface port."""

from __future__ import annotations

import json
import os
import pathlib
import urllib.request

port_file = pathlib.Path("/tmp/uc-advanced-automations-web-port")
try:
    port = int(port_file.read_text(encoding="utf-8").strip())
except (OSError, ValueError):
    port = int(os.environ.get("UC_AUTOMATIONS_WEB_PORT", "8099"))

with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
    payload = json.load(response)
if payload.get("status") != "ok":
    raise SystemExit(1)
