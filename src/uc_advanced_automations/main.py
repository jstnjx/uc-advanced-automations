"""Application entry point."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from importlib.resources import files
from typing import Any, Awaitable, Callable

import ucapi

from .runtime import detect_runtime
from .startup import initialize_integration_api

_LOG = logging.getLogger(__name__)


class _DeferredSetupHandler:
    """Accept Core setup requests while the full application finishes loading."""

    def __init__(self) -> None:
        self._ready = asyncio.Event()
        self._delegate: Callable[[Any], Awaitable[Any]] | None = None
        self._startup_error: str | None = None

    def set_delegate(self, delegate: Callable[[Any], Awaitable[Any]]) -> None:
        self._delegate = delegate
        self._ready.set()

    def set_error(self, error: BaseException) -> None:
        self._startup_error = f"{type(error).__name__}: {error}"
        self._ready.set()

    async def __call__(self, message: Any) -> Any:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=25)
        except TimeoutError:
            _LOG.error("Setup requested before application initialization completed")
            return ucapi.SetupError(error_type=ucapi.IntegrationSetupError.OTHER)
        if self._delegate is None:
            _LOG.error("Setup unavailable after startup failure: %s", self._startup_error)
            return ucapi.SetupError(error_type=ucapi.IntegrationSetupError.OTHER)
        return await self._delegate(message)


async def run() -> None:
    runtime = detect_runtime()
    runtime.apply_process_environment(9090)

    loop = asyncio.get_running_loop()
    api = ucapi.IntegrationAPI(loop)
    setup_handler = _DeferredSetupHandler()
    service_status: dict[str, Any] = {
        "integration_api_ready": False,
        "integration_api_error": None,
        "application_ready": False,
        "application_error": None,
        "web_ready": False,
        "web_error": None,
        "entity_definition_ready": False,
        "entity_definition_error": None,
        "trigger_manager_ready": False,
        "trigger_manager_error": None,
    }

    @api.listens_to(ucapi.Events.CONNECT)
    async def on_connect() -> None:
        await api.set_device_state(ucapi.DeviceStates.CONNECTED)

    @api.listens_to(ucapi.Events.DISCONNECT)
    async def on_disconnect() -> None:
        _LOG.info("Remote disconnected from integration")

    driver_path = str(files("uc_advanced_automations").joinpath("driver.json"))
    _LOG.info(
        "Starting Advanced Automations v1.0.9 bootstrap: runtime=%s integration=%s:%s",
        runtime.mode,
        os.environ.get("UC_INTEGRATION_INTERFACE", "0.0.0.0"),
        os.environ.get("UC_INTEGRATION_HTTP_PORT", "9090"),
    )

    # Bind the socket Core is waiting for before importing/initializing the
    # database, automation engine, trigger scheduler or browser editor.
    integration_ready = await initialize_integration_api(
        api,
        driver_path,
        setup_handler,
        service_status,
    )
    if not integration_ready and runtime.runs_on_remote:
        raise RuntimeError(service_status["integration_api_error"] or "Integration API failed")

    runner: Any | None = None
    database: Any | None = None
    core: Any | None = None
    engine: Any | None = None
    triggers: Any | None = None

    try:
        # Deliberately lazy-import the larger application stack after ucapi has
        # opened the Core-facing listener. This is material on Remote hardware
        # and in a cold PyInstaller process.
        from aiohttp import web

        from .config_store import ConfigStore
        from .core_client import CoreClient
        from .database import AutomationDatabase
        from .engine import AutomationEngine
        from .integration import IntegrationController
        from .setup_flow import RemoteApiSetupFlow
        from .startup import start_web_site
        from .triggers import TriggerManager
        from .web import create_app

        store = ConfigStore(runtime=runtime)
        service_status.update(store.recovery_status)
        settings = store.settings()
        _LOG.info(
            "Initializing application services: data_dir=%s web=%s:%d",
            store.data_dir,
            settings.web_host,
            settings.web_port,
        )

        database = AutomationDatabase(store.data_dir)
        core = CoreClient(store.settings)
        engine = AutomationEngine(core, lambda: store.settings().timezone, database)
        integration = IntegrationController(api, store, engine, core, database)
        triggers = TriggerManager(core, store, engine)

        try:
            integration.sync_entity()
            service_status["entity_definition_ready"] = True
        except Exception as err:  # pragma: no cover - protects startup
            service_status["entity_definition_error"] = f"{type(err).__name__}: {err}"
            _LOG.exception("Unable to build the initial integration entity")
        try:
            triggers.start()
            service_status["trigger_manager_ready"] = True
        except Exception as err:  # pragma: no cover - protects startup
            service_status["trigger_manager_error"] = f"{type(err).__name__}: {err}"
            _LOG.exception("Unable to start background triggers")

        setup_flow = RemoteApiSetupFlow(store, core, on_settings_changed=triggers.reload)
        setup_handler.set_delegate(setup_flow.handle)
        service_status["application_ready"] = True
        service_status["application_error"] = None

        app = create_app(
            store,
            core,
            engine,
            integration,
            triggers,
            runtime,
            database,
            service_status,
        )
        runner = web.AppRunner(app, access_log=_LOG)
        await runner.setup()
        try:
            integration_port = int(os.environ.get("UC_INTEGRATION_HTTP_PORT", "9090"))
        except ValueError:
            integration_port = 9090
        try:
            _site, actual_web_port = await start_web_site(
                runner,
                settings.web_host,
                settings.web_port,
                integration_port,
                service_status,
            )
            _LOG.info("AUTOMATION EDITOR URL: http://%s:%d/", settings.web_host, actual_web_port)
        except Exception as err:  # The Integration API must remain usable without the editor.
            service_status["web_ready"] = False
            service_status["web_error"] = f"{type(err).__name__}: {err}"
            _LOG.exception("Web editor could not be started; Integration API remains available")
    except Exception as err:
        service_status["application_ready"] = False
        service_status["application_error"] = f"{type(err).__name__}: {err}"
        setup_handler.set_error(err)
        _LOG.exception("Application services failed after Integration API startup")
        if not integration_ready:
            raise

    stop_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:  # Windows
            pass

    try:
        await stop_event.wait()
    finally:
        if triggers is not None:
            await triggers.close()
        if engine is not None:
            await engine.close()
        if core is not None:
            await core.close()
        if runner is not None:
            await runner.cleanup()
        if database is not None:
            database.close()


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
