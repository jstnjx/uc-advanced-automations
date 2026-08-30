"""Application entry point."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from importlib.resources import files
from typing import Any, Awaitable, Callable

import ucapi

from . import __version__
from .framework_driver import AdvancedAutomationsDriver
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
    # ucapi-framework owns the IntegrationAPI instance and standard Remote
    # lifecycle/event wiring. Advanced Automations only supplies its domain
    # setup flow and dynamically generated entities.
    framework_driver = AdvancedAutomationsDriver(loop=loop)
    api = framework_driver.api
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
        "integration_framework": "ucapi-framework",
        "core_client": "unfurled",
    }

    driver_path = str(files("uc_advanced_automations").joinpath("driver.json"))
    _LOG.info(
        "Starting Advanced Automations v%s: runtime=%s integration=%s:%s framework=ucapi-framework core=unfurled",
        __version__,
        runtime.mode,
        os.environ.get("UC_INTEGRATION_INTERFACE", "0.0.0.0"),
        os.environ.get("UC_INTEGRATION_HTTP_PORT", "9090"),
    )

    # Bind the Core-facing listener before importing the larger editor/database
    # stack. The framework's IntegrationAPI preserves the fast embedded startup
    # path required by Remote hardware.
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
        except Exception as err:
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
        except NotImplementedError:
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
