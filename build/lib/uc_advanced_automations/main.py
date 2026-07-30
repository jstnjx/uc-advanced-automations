"""Application entry point."""

from __future__ import annotations

import asyncio
import logging
import signal
from importlib.resources import files

import ucapi
from aiohttp import web

from .config_store import ConfigStore
from .core_client import CoreClient
from .engine import AutomationEngine
from .integration import IntegrationController
from .web import create_app

_LOG = logging.getLogger(__name__)


async def run() -> None:
    store = ConfigStore()
    settings = store.settings()
    loop = asyncio.get_running_loop()
    api = ucapi.IntegrationAPI(loop)
    core = CoreClient(store.settings)
    engine = AutomationEngine(core, lambda: store.settings().timezone)
    integration = IntegrationController(api, store, engine)
    integration.sync_entity()

    @api.listens_to(ucapi.Events.CONNECT)
    async def on_connect() -> None:
        await api.set_device_state(ucapi.DeviceStates.CONNECTED)

    @api.listens_to(ucapi.Events.DISCONNECT)
    async def on_disconnect() -> None:
        _LOG.info("Remote disconnected from integration")

    async def setup_handler(_message: ucapi.SetupDriver) -> ucapi.SetupAction:
        """Complete the informational setup flow shown by driver.json."""
        return ucapi.SetupComplete()

    driver_path = files("uc_advanced_automations").joinpath("driver.json")
    await api.init(str(driver_path), setup_handler)

    app = create_app(store, core, engine, integration)
    runner = web.AppRunner(app, access_log=_LOG)
    await runner.setup()
    site = web.TCPSite(runner, settings.web_host, settings.web_port)
    await site.start()
    _LOG.info("Web interface listening on http://%s:%d", settings.web_host, settings.web_port)

    stop_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:  # Windows
            pass

    try:
        await stop_event.wait()
    finally:
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


if __name__ == "__main__":
    main()
