# Advanced Automations v2.0.0
"""Application entry point."""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import signal
from importlib.resources import files
from typing import Any, Awaitable, Callable

import ucapi

from . import __version__
from .runtime import detect_runtime
from .startup import initialize_integration_api

_LOG = logging.getLogger(__name__)


class _DeferredSetupHandler:
    """Accept Core setup requests while the full application finishes loading."""

    def __init__(self, activate: Callable[[], None] | None = None) -> None:
        self._ready = asyncio.Event()
        self._delegate: Callable[[Any], Awaitable[Any]] | None = None
        self._startup_error: str | None = None
        self._activate = activate

    def set_delegate(self, delegate: Callable[[Any], Awaitable[Any]]) -> None:
        self._delegate = delegate
        self._ready.set()

    def set_error(self, error: BaseException) -> None:
        self._startup_error = f"{type(error).__name__}: {error}"
        self._ready.set()

    async def __call__(self, message: Any) -> Any:
        # ucapi acknowledges setup_driver before invoking this handler. Activating
        # here means the Core-facing WebSocket handshake and setup request are
        # already complete before expensive framework/application imports begin.
        if self._activate is not None:
            self._activate()
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=45)
        except TimeoutError:
            _LOG.error("Setup requested before application initialization completed")
            return ucapi.SetupError(error_type=ucapi.IntegrationSetupError.OTHER)
        if self._delegate is None:
            _LOG.error("Setup unavailable after startup failure: %s", self._startup_error)
            return ucapi.SetupError(error_type=ucapi.IntegrationSetupError.OTHER)
        return await self._delegate(message)


async def _attach_framework_driver(loop: asyncio.AbstractEventLoop, api: ucapi.IntegrationAPI) -> Any:
    """Load ucapi-framework after Core has activated the bootstrap API session."""

    module = await asyncio.to_thread(
        importlib.import_module,
        "uc_advanced_automations.framework_driver",
    )
    return module.AdvancedAutomationsDriver(loop=loop, api=api)


def _restore_early_subscriptions(api: ucapi.IntegrationAPI, entity_ids: set[str]) -> list[str]:
    """Replay subscriptions received before dynamic entities were registered."""

    restored: list[str] = []
    for entity_id in sorted(entity_ids):
        if api.configured_entities.contains(entity_id):
            continue
        entity = api.available_entities.get(entity_id)
        if entity is None:
            continue
        api.configured_entities.add(entity)
        restored.append(entity_id)
    return restored


async def run() -> None:
    runtime = detect_runtime()
    runtime.apply_process_environment(9090)

    loop = asyncio.get_running_loop()
    # The Remote Core has a short custom-integration startup deadline. Build only
    # the minimal ucapi listener first. On Remote hardware no expensive framework,
    # Unfurled, database or editor import may start until Core has successfully
    # delivered setup_driver or connect over this listener.
    api = ucapi.IntegrationAPI(loop)
    activation_event = asyncio.Event()
    activation_reason: str | None = None
    bootstrap_connect_received = False
    pending_subscriptions: set[str] = set()

    service_status: dict[str, Any] = {
        "integration_api_ready": False,
        "integration_api_error": None,
        "bootstrap_activation": None,
        "framework_attached": False,
        "framework_error": None,
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

    def activate(reason: str) -> None:
        nonlocal activation_reason
        if activation_event.is_set():
            return
        activation_reason = reason
        service_status["bootstrap_activation"] = reason
        _LOG.info("Core activated bootstrap Integration API via %s", reason)
        activation_event.set()

    setup_handler = _DeferredSetupHandler(lambda: activate("setup_driver"))

    @api.listens_to(ucapi.Events.CONNECT)
    async def _bootstrap_connect() -> None:
        nonlocal bootstrap_connect_received
        if service_status["framework_attached"]:
            return
        bootstrap_connect_received = True
        activate("connect")
        # The framework normally performs this in its CONNECT handler. The first
        # CONNECT deliberately arrives before the framework is loaded, so preserve
        # the observable device state here. There are no framework-managed device
        # instances in Advanced Automations.
        await api.set_device_state(ucapi.DeviceStates.CONNECTED)

    @api.listens_to(ucapi.Events.SUBSCRIBE_ENTITIES)
    def _capture_early_subscriptions(entity_ids: list[str]) -> None:
        if service_status["application_ready"]:
            return
        pending_subscriptions.update(str(entity_id) for entity_id in entity_ids)

    framework_driver: Any | None = None
    driver_path = str(files("uc_advanced_automations").joinpath("driver.json"))
    _LOG.info(
        "Starting Advanced Automations v%s: runtime=%s integration=%s:%s framework=ucapi-framework core=unfurled",
        __version__,
        runtime.mode,
        os.environ.get("UC_INTEGRATION_INTERFACE", "0.0.0.0"),
        os.environ.get("UC_INTEGRATION_HTTP_PORT", "9090"),
    )

    integration_ready = await initialize_integration_api(
        api,
        driver_path,
        setup_handler,
        service_status,
    )
    if not integration_ready and runtime.runs_on_remote:
        raise RuntimeError(service_status["integration_api_error"] or "Integration API failed")

    if runtime.runs_on_remote:
        _LOG.info(
            "Integration API is listening; deferring framework/application startup until Core sends setup_driver or connect"
        )
        await activation_event.wait()
        # Let ucapi finish the protocol callback that triggered activation before
        # starting imports. In particular, setup_driver has already been ACKed.
        await asyncio.sleep(0)
    else:
        activate("external_runtime")

    try:
        framework_driver = await _attach_framework_driver(loop, api)
        service_status["framework_attached"] = True
        service_status["framework_error"] = None
        _LOG.info(
            "ucapi-framework lifecycle attached after bootstrap activation (%s)",
            activation_reason,
        )
    except Exception as err:
        service_status["framework_attached"] = False
        service_status["framework_error"] = f"{type(err).__name__}: {err}"
        setup_handler.set_error(err)
        _LOG.exception("Unable to attach ucapi-framework lifecycle")
        if runtime.runs_on_remote:
            raise

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
        from .extended_engine import ExtendedAutomationEngine
        from .integration import IntegrationController
        from .setup_flow import RemoteApiSetupFlow
        from .startup import start_web_site
        from .step_model_extensions import install_model_extensions
        from .triggers import TriggerManager
        from .web import create_app

        install_model_extensions()
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
        engine = ExtendedAutomationEngine(
            core,
            lambda: store.settings().timezone,
            database,
            store.get_automation,
        )
        integration = IntegrationController(api, store, engine, core, database)
        triggers = TriggerManager(core, store, engine)

        try:
            integration.sync_entity()
            restored = _restore_early_subscriptions(api, pending_subscriptions)
            pending_subscriptions.clear()
            if restored:
                _LOG.info("Restored early entity subscriptions: %s", ", ".join(restored))
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

        # If the bootstrap CONNECT was the activation trigger, framework loading
        # necessarily happened after that first event. Its no-op runtime device has
        # already been represented by the CONNECTED state above; subsequent lifecycle
        # events are handled by ucapi-framework normally.
        if bootstrap_connect_received:
            _LOG.info("Bootstrap CONNECT completed before framework attachment")

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
        framework_driver = None


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
