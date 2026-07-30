"""Startup helpers shared by the runtime and tests."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from aiohttp import web

_LOG = logging.getLogger(__name__)


async def initialize_integration_api(
    api: Any,
    driver_path: str,
    setup_handler: Any,
    service_status: dict[str, Any],
) -> bool:
    """Initialize ucapi and expose failures through service diagnostics.

    The caller keeps the web process alive so an external installer can inspect
    ``/api/status`` and ``/api/health`` instead of only seeing an exited container.
    """

    try:
        await api.init(driver_path, setup_handler)
    except Exception as err:  # pragma: no cover - exact ucapi/platform error varies
        service_status["integration_api_ready"] = False
        service_status["integration_api_error"] = f"{type(err).__name__}: {err}"
        _LOG.exception("Integration API initialization failed; web diagnostics remain available")
        return False

    service_status["integration_api_ready"] = True
    service_status["integration_api_error"] = None
    _LOG.info("Integration API initialized")
    return True


def _web_port_candidates(requested_port: int, integration_port: int) -> list[int]:
    """Return ordered web-port candidates avoiding the Integration API port."""

    candidates: list[int] = []

    def add(port: int) -> None:
        if 1 <= port <= 65535 and port != integration_port and port not in candidates:
            candidates.append(port)

    add(requested_port)
    add(integration_port + 10000)
    for offset in range(1, 33):
        add(requested_port + offset)
    # Port 0 asks the OS for a free ephemeral port and is the final fallback.
    candidates.append(0)
    return candidates


async def start_web_site(
    runner: web.AppRunner,
    host: str,
    requested_port: int,
    integration_port: int,
    service_status: dict[str, Any],
    port_file: str | os.PathLike[str] = "/tmp/uc-advanced-automations-web-port",
) -> tuple[web.TCPSite, int]:
    """Start the web UI without colliding with the Integration API or host services."""

    last_error: OSError | None = None
    for candidate in _web_port_candidates(requested_port, integration_port):
        site = web.TCPSite(runner, host, candidate)
        try:
            await site.start()
        except OSError as err:
            last_error = err
            _LOG.warning("Unable to bind web interface to %s:%s: %s", host, candidate, err)
            try:
                await site.stop()
            except Exception:  # pragma: no cover - aiohttp cleanup safety
                pass
            continue

        actual_port = candidate
        if candidate == 0:
            addresses = getattr(runner, "addresses", [])
            if addresses:
                actual_port = int(addresses[0][1])
        service_status["web_port_requested"] = requested_port
        service_status["web_port"] = actual_port
        service_status["web_port_fallback"] = actual_port != requested_port
        try:
            Path(port_file).write_text(f"{actual_port}\n", encoding="utf-8")
        except OSError:
            _LOG.warning("Unable to write web-port healthcheck file: %s", port_file)
        if actual_port != requested_port:
            _LOG.warning(
                "Configured web port %d was unavailable or conflicted with the Integration API; using %d",
                requested_port,
                actual_port,
            )
        return site, actual_port

    if last_error is not None:
        raise last_error
    raise OSError("Unable to allocate a web-interface port")
