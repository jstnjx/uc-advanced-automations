"""Application entry point."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from importlib.resources import files
from typing import Any

import ucapi
from aiohttp import web

from .config_store import ConfigStore
from .core_client import CoreClient
from .engine import AutomationEngine
from .integration import IntegrationController
from .runtime import detect_runtime
from .startup import initialize_integration_api, start_web_site
from .triggers import TriggerManager
from .web import create_app

_LOG = logging.getLogger(__name__)


async def run() -> None:
    runtime = detect_runtime()
    store = ConfigStore(runtime=runtime)
    settings = store.settings()
    runtime.apply_process_environment(9090)

    _LOG.info(
        "Starting Advanced Automations v0.3.1: runtime=%s data_dir=%s web=%s:%d "
        "integration=%s:%s mdns_disabled=%s",
        runtime.mode,
        store.data_dir,
        settings.web_host,
        settings.web_port,
        os.environ.get("UC_INTEGRATION_INTERFACE", "0.0.0.0"),
        os.environ.get("UC_INTEGRATION_HTTP_PORT", "9090"),
        os.environ.get("UC_DISABLE_MDNS_PUBLISH", "false"),
    )

    loop = asyncio.get_running_loop()
    api = ucapi.IntegrationAPI(loop)
    core = CoreClient(store.settings)
    engine = AutomationEngine(core, lambda: store.settings().timezone)
    integration = IntegrationController(api, store, engine, core)
    integration.sync_entity()
    triggers = TriggerManager(core, store, engine)
    triggers.start()

    @api.listens_to(ucapi.Events.CONNECT)
    async def on_connect() -> None:
        await api.set_device_state(ucapi.DeviceStates.CONNECTED)

    @api.listens_to(ucapi.Events.DISCONNECT)
    async def on_disconnect() -> None:
        _LOG.info("Remote disconnected from integration")

    async def setup_handler(_message: ucapi.SetupDriver) -> ucapi.SetupAction:
        """Complete the informational setup flow shown by driver.json."""
        return ucapi.SetupComplete()

    service_status: dict[str, Any] = {
        "integration_api_ready": False,
        "integration_api_error": None,
    }
    driver_path = str(files("uc_advanced_automations").joinpath("driver.json"))

    app = create_app(store, core, engine, integration, triggers, runtime, service_status)
    runner = web.AppRunner(app, access_log=_LOG)
    await runner.setup()
    try:
        integration_port = int(os.environ.get("UC_INTEGRATION_HTTP_PORT", "9090"))
    except ValueError:
        integration_port = 9090
    _site, actual_web_port = await start_web_site(
        runner,
        settings.web_host,
        settings.web_port,
        integration_port,
        service_status,
    )
    _LOG.info(
        "Web interface listening on http://%s:%d",
        settings.web_host,
        actual_web_port,
    )

    # ucapi initialization is isolated from the web server lifecycle. In
    # particular, a zeroconf failure must not make the external container exit.
    integration_init_task = asyncio.create_task(
        initialize_integration_api(api, driver_path, setup_handler, service_status),
        name="integration-api-init",
    )

    stop_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:  # Windows
            pass

    try:
        await stop_event.wait()
    finally:
        if not integration_init_task.done():
            integration_init_task.cancel()
        await asyncio.gather(integration_init_task, return_exceptions=True)
        await triggers.close()
        await engine.close()
        await core.close()
        await runner.cleanup()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except Exception:
        _LOG.exception("Fatal startup failure")
        raise


if __name__ == "__main__":
    main()
