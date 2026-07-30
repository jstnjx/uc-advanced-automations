"""Aiohttp web application and JSON API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web
from pydantic import ValidationError

from .config_store import ConfigStore
from .core_client import CoreApiError, CoreClient
from .engine import AutomationEngine
from .models import Automation, Settings
from .runtime import RuntimeEnvironment
from .triggers import TriggerManager

if TYPE_CHECKING:
    from .integration import IntegrationController

STATIC_DIR = Path(__file__).parent / "static"


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except ValidationError as err:
        return web.json_response({"error": "Validation failed", "details": err.errors()}, status=400)
    except CoreApiError as err:
        return web.json_response({"error": str(err)}, status=err.code if 400 <= err.code <= 599 else 500)
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    except web.HTTPException:
        raise
    except Exception as err:  # pragma: no cover - final safety net
        return web.json_response({"error": str(err)}, status=500)


def create_app(
    store: ConfigStore,
    core: CoreClient,
    engine: AutomationEngine,
    integration: "IntegrationController",
    triggers: TriggerManager,
    runtime: RuntimeEnvironment,
    service_status: dict[str, Any] | None = None,
) -> web.Application:
    app = web.Application(middlewares=[error_middleware], client_max_size=2 * 1024 * 1024)
    app.update(
        store=store,
        core=core,
        engine=engine,
        integration=integration,
        triggers=triggers,
        runtime=runtime,
        service_status=service_status if service_status is not None else {},
    )

    app.router.add_get("/", index)
    app.router.add_get("/api/health", get_health)
    app.router.add_get("/api/status", get_status)
    app.router.add_get("/api/settings", get_settings)
    app.router.add_put("/api/settings", update_settings)
    app.router.add_post("/api/settings/test", test_connection)
    app.router.add_get("/api/entities", get_entities)
    app.router.add_get("/api/entities/{entity_id}/commands", get_entity_commands)
    app.router.add_post("/api/integration/refresh", refresh_integration_entity)
    app.router.add_get("/api/automations", get_automations)
    app.router.add_post("/api/automations", create_automation)
    app.router.add_put("/api/automations/{automation_id}", update_automation)
    app.router.add_delete("/api/automations/{automation_id}", delete_automation)
    app.router.add_post("/api/automations/{automation_id}/run", run_automation)
    app.router.add_get("/api/logs", get_logs)
    app.router.add_static("/static/", STATIC_DIR, show_index=False)
    return app


async def index(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def get_health(request: web.Request) -> web.Response:
    """Container liveness endpoint; detailed readiness remains in the payload."""

    status = request.app["service_status"]
    core: CoreClient = request.app["core"]
    runtime: RuntimeEnvironment = request.app["runtime"]
    return web.json_response(
        {
            "status": "ok",
            "runtime_mode": runtime.mode,
            "integration_api_ready": bool(status.get("integration_api_ready")),
            "integration_api_error": status.get("integration_api_error"),
            "core_connected": core.is_connected,
            "web_port": status.get("web_port"),
            "web_port_fallback": bool(status.get("web_port_fallback")),
        }
    )


async def get_status(request: web.Request) -> web.Response:
    core: CoreClient = request.app["core"]
    engine: AutomationEngine = request.app["engine"]
    store: ConfigStore = request.app["store"]
    triggers: TriggerManager = request.app["triggers"]
    runtime: RuntimeEnvironment = request.app["runtime"]
    integration = request.app["integration"]
    return web.json_response(
        {
            "core_connected": core.is_connected,
            "core_error": core.last_error,
            "api_key_configured": bool(store.settings().api_key),
            "running": engine.running_count(),
            "automation_count": len(store.automations()),
            "trigger_count": triggers.trigger_count,
            "trigger_entities": triggers.tracked_entity_count,
            "trigger_error": triggers.last_error,
            "entity_refresh": integration.last_refresh,
            "runtime_mode": runtime.mode,
            "runtime_name": runtime.display_name,
            "runs_on_remote": runtime.runs_on_remote,
            "web_port": request.app["service_status"].get("web_port", store.settings().web_port),
            "web_port_configured": store.settings().web_port,
            "services": dict(request.app["service_status"]),
        }
    )


async def get_settings(request: web.Request) -> web.Response:
    settings = request.app["store"].settings().model_dump()
    runtime: RuntimeEnvironment = request.app["runtime"]
    api_key = settings.pop("api_key")
    settings["api_key"] = ""
    settings["api_key_configured"] = bool(api_key)
    settings["runtime_mode"] = runtime.mode
    settings["runtime_name"] = runtime.display_name
    settings["runs_on_remote"] = runtime.runs_on_remote
    settings["data_dir"] = str(request.app["store"].data_dir)
    return web.json_response(settings)


async def update_settings(request: web.Request) -> web.Response:
    store: ConfigStore = request.app["store"]
    core: CoreClient = request.app["core"]
    triggers: TriggerManager = request.app["triggers"]
    body = await request.json()
    current = store.settings()
    if not body.get("api_key"):
        body["api_key"] = current.api_key
    settings = Settings.model_validate(body)
    store.update_settings(settings)
    await core.close()
    triggers.reload()
    response = settings.model_dump()
    response["api_key"] = ""
    response["api_key_configured"] = bool(settings.api_key)
    response["restart_required"] = settings.web_host != current.web_host or settings.web_port != current.web_port
    return web.json_response(response)


async def test_connection(request: web.Request) -> web.Response:
    core: CoreClient = request.app["core"]
    return web.json_response(await core.test_connection())


async def get_entities(request: web.Request) -> web.Response:
    core: CoreClient = request.app["core"]
    entities = await core.get_entities()
    clean = []
    for entity in entities:
        clean.append(
            {
                "entity_id": entity.get("entity_id"),
                "entity_type": entity.get("entity_type"),
                "name": entity.get("name"),
                "integration_id": entity.get("integration_id"),
                "features": entity.get("features", []),
                "attributes": entity.get("attributes", {}),
                "options": entity.get("options", {}),
            }
        )
    clean.sort(key=lambda item: (_display_name(item).lower(), str(item.get("entity_id", ""))))
    return web.json_response({"entities": clean})


async def get_entity_commands(request: web.Request) -> web.Response:
    core: CoreClient = request.app["core"]
    return web.json_response(await core.get_command_definitions(request.match_info["entity_id"]))


async def refresh_integration_entity(request: web.Request) -> web.Response:
    result = await request.app["integration"].sync_and_refresh(force=True)
    return web.json_response(result, status=200 if result.get("status") != "failed" else 503)


async def get_automations(request: web.Request) -> web.Response:
    return web.json_response(
        {"automations": [item.model_dump(mode="json") for item in request.app["store"].automations()]}
    )


async def create_automation(request: web.Request) -> web.Response:
    store: ConfigStore = request.app["store"]
    automation = Automation.model_validate(await request.json())

    def mutate(config):
        config.automations.append(automation)

    store.mutate(mutate)
    refresh = await _apply_runtime_changes(request)
    response = web.json_response(automation.model_dump(mode="json"), status=201)
    _set_refresh_headers(response, refresh)
    return response


async def update_automation(request: web.Request) -> web.Response:
    store: ConfigStore = request.app["store"]
    automation_id = request.match_info["automation_id"]
    body = await request.json()
    body["id"] = automation_id
    automation = Automation.model_validate(body)
    found = False

    def mutate(config):
        nonlocal found
        for index, current in enumerate(config.automations):
            if current.id == automation_id:
                config.automations[index] = automation
                found = True
                break

    store.mutate(mutate)
    if not found:
        raise web.HTTPNotFound(text=json.dumps({"error": "Automation not found"}), content_type="application/json")
    refresh = await _apply_runtime_changes(request)
    response = web.json_response(automation.model_dump(mode="json"))
    _set_refresh_headers(response, refresh)
    return response


async def delete_automation(request: web.Request) -> web.Response:
    store: ConfigStore = request.app["store"]
    automation_id = request.match_info["automation_id"]
    before = len(store.automations())

    def mutate(config):
        config.automations = [item for item in config.automations if item.id != automation_id]

    store.mutate(mutate)
    if len(store.automations()) == before:
        raise web.HTTPNotFound(text=json.dumps({"error": "Automation not found"}), content_type="application/json")
    refresh = await _apply_runtime_changes(request)
    response = web.Response(status=204)
    _set_refresh_headers(response, refresh)
    return response


async def run_automation(request: web.Request) -> web.Response:
    store: ConfigStore = request.app["store"]
    engine: AutomationEngine = request.app["engine"]
    automation = store.get_automation(request.match_info["automation_id"])
    if not automation:
        raise web.HTTPNotFound(text=json.dumps({"error": "Automation not found"}), content_type="application/json")
    result = engine.start(automation, source="web interface")
    status = 202 if result.accepted else 409
    return web.json_response({"accepted": result.accepted, "run_id": result.run_id, "reason": result.reason}, status=status)


async def get_logs(request: web.Request) -> web.Response:
    engine: AutomationEngine = request.app["engine"]
    try:
        after = int(request.query.get("after", "0"))
    except ValueError:
        after = 0
    return web.json_response({"logs": engine.logs_after(after)})


async def _apply_runtime_changes(request: web.Request) -> dict[str, Any]:
    triggers: TriggerManager = request.app["triggers"]
    triggers.reload()
    return await request.app["integration"].sync_and_refresh()


def _set_refresh_headers(response: web.StreamResponse, refresh: dict[str, Any]) -> None:
    response.headers["X-UC-Entity-Refresh"] = str(refresh.get("status", "unknown"))
    if refresh.get("message"):
        response.headers["X-UC-Entity-Refresh-Message"] = str(refresh["message"])[:500]


def _display_name(entity: dict[str, Any]) -> str:
    name = entity.get("name")
    if isinstance(name, str):
        return name
    if isinstance(name, dict):
        return str(name.get("en") or next(iter(name.values()), ""))
    return str(entity.get("entity_id", ""))
