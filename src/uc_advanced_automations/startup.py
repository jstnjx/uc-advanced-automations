"""Startup helpers shared by embedded and external runtimes."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)


async def initialize_integration_api(
    api: Any,
    driver_path: str,
    setup_handler: Any,
    service_status: dict[str, Any],
) -> bool:
    """Initialize ucapi before optional application services.

    On the Remote the Core only waits a short time for the Integration API
    listener. Starting this listener before SQLite, triggers and the web editor
    prevents those optional services from delaying or blocking setup.
    """

    try:
        await api.init(driver_path, setup_handler)
        # ``ucapi.IntegrationAPI.init`` schedules the websocket listener in a
        # background task. Yield until that task has had an opportunity to bind,
        # and surface an immediate bind/import failure instead of reporting a
        # misleading successful initialization.
        for _ in range(40):
            await asyncio.sleep(0.025)
            server_task = getattr(api, "_server_task", None)
            if server_task is None:
                continue
            if server_task.done():
                exception = server_task.exception()
                if exception is not None:
                    raise exception
                raise RuntimeError("Integration API listener stopped during startup")
            # A pending server task after multiple event-loop turns means the
            # websocket serve context has entered its long-running wait.
            if _ >= 1:
                break
    except Exception as err:  # pragma: no cover - platform error varies
        service_status["integration_api_ready"] = False
        service_status["integration_api_error"] = f"{type(err).__name__}: {err}"
        _LOG.exception("Integration API initialization failed")
        return False

    service_status["integration_api_ready"] = True
    service_status["integration_api_error"] = None
    _LOG.info(
        "Integration API initialized on %s:%s",
        os.environ.get("UC_INTEGRATION_INTERFACE", "0.0.0.0"),
        os.environ.get("UC_INTEGRATION_HTTP_PORT", "9090"),
    )
    return True


def _web_port_candidates(
    requested_port: int,
    integration_port: int,
    *,
    max_attempts: int = 128,
) -> list[int]:
    """Return bounded editor-port candidates outside the reserved API range."""

    first = max(9201, requested_port)
    candidates: list[int] = []
    candidate = first
    while len(candidates) < max_attempts and candidate <= 65535:
        if candidate != integration_port:
            candidates.append(candidate)
        candidate += 1
    return candidates


async def start_web_site(
    runner: Any,
    host: str,
    requested_port: int,
    integration_port: int,
    service_status: dict[str, Any],
    port_file: str | os.PathLike[str] = "/tmp/uc-advanced-automations-web-port",
) -> tuple[Any, int]:
    """Start the optional editor without delaying the Integration API listener."""

    # Keep aiohttp out of the embedded bootstrap import path. The Remote Core
    # can connect to ucapi while the larger web stack is imported afterwards.
    from aiohttp import web

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
        service_status["web_port_requested"] = requested_port
        service_status["web_port"] = actual_port
        service_status["web_port_fallback"] = actual_port != requested_port
        service_status["web_ready"] = True
        service_status["web_error"] = None
        try:
            Path(port_file).write_text(f"{actual_port}\n", encoding="utf-8")
        except OSError:
            _LOG.warning("Unable to write web-port healthcheck file: %s", port_file)
        data_dir = os.environ.get("UC_AUTOMATIONS_DATA_DIR")
        if data_dir:
            try:
                Path(data_dir, "web-port.txt").write_text(f"{actual_port}\n", encoding="utf-8")
            except OSError:
                _LOG.warning("Unable to write persistent web-port discovery file in %s", data_dir)
        if actual_port != requested_port:
            _LOG.warning(
                "Configured web port %d was unavailable or conflicted with the Integration API; using %d",
                requested_port,
                actual_port,
            )
        return site, actual_port

    service_status["web_ready"] = False
    service_status["web_error"] = (
        f"Unable to allocate an editor port after 128 attempts: {last_error}"
        if last_error is not None
        else "Unable to allocate an editor port"
    )
    if last_error is not None:
        raise last_error
    raise OSError("Unable to allocate a web-interface port")
