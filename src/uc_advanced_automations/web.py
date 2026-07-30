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
if TYPE_CHECKING:
    from .integration import IntegrationController
from .models import Automation, Settings
from .runtime import RuntimeEnvironment

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
    runtime: RuntimeEnvironment,
) -> web.Application:
    app = web.Application(middlewares=[error_middleware], client_max_size=2 * 1024 * 1024)
    app["store"] = store
    app["core"] = core
    app["engine"] = engine
    app["integration"] = integration
    app["runtime"] = runtime

    app.router.add_get("/", index)
    app.router.add_get("/api/status", get_status)
    app.router.add_get("/api/settings", get_settings)
    app.router.add_put("/api/settings", update_settings)
    app.router.add_post("/api/settings/test", test_connection)
    app.router.add_get("/api/entities", get_entities)
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


async def get_status(request: web.Request) -> web.Response:
    core: CoreClient = request.app["core"]
    engine: AutomationEngine = request.app["engine"]
    store: ConfigStore = request.app["store"]
    runtime: RuntimeEnvironment = request.app["runtime"]
    return web.json_response(
        {
            "core_connected": core.is_connected,
            "core_error": core.last_error,
            "api_key_configured": bool(store.settings().api_key),
            "running": engine.running_count(),
            "automation_count": len(store.automations()),
            "runtime_mode": runtime.mode,
            "runtime_name": runtime.display_name,
            "runs_on_remote": runtime.runs_on_remote,
            "web_port": store.settings().web_port,
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
    body = await request.json()
    current = store.settings()
    if not body.get("api_key"):
        body["api_key"] = current.api_key
    settings = Settings.model_validate(body)
    store.update_settings(settings)
    await core.close()
    response = settings.model_dump()
    response["api_key"] = ""
    response["api_key_configured"] = bool(settings.api_key)
    response["restart_required"] = (
        settings.web_host != current.web_host or settings.web_port != current.web_port
    )
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
            }
        )
    clean.sort(key=lambda item: (_display_name(item).lower(), str(item.get("entity_id", ""))))
    return web.json_response({"entities": clean})


async def get_automations(request: web.Request) -> web.Response:
    return web.json_response(
        {"automations": [item.model_dump(mode="json") for item in request.app["store"].automations()]}
    )


async def create_automation(request: web.Request) -> web.Response:
    store: ConfigStore = request.app["store"]
    integration = request.app["integration"]
    automation = Automation.model_validate(await request.json())

    def mutate(config):
        config.automations.append(automation)

    store.mutate(mutate)
    integration.sync_entity()
    return web.json_response(automation.model_dump(mode="json"), status=201)


async def update_automation(request: web.Request) -> web.Response:
    store: ConfigStore = request.app["store"]
    integration = request.app["integration"]
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
    integration.sync_entity()
    return web.json_response(automation.model_dump(mode="json"))


async def delete_automation(request: web.Request) -> web.Response:
    store: ConfigStore = request.app["store"]
    integration = request.app["integration"]
    automation_id = request.match_info["automation_id"]
    before = len(store.automations())

    def mutate(config):
        config.automations = [item for item in config.automations if item.id != automation_id]

    store.mutate(mutate)
    if len(store.automations()) == before:
        raise web.HTTPNotFound(text=json.dumps({"error": "Automation not found"}), content_type="application/json")
    integration.sync_entity()
    return web.Response(status=204)


async def run_automation(request: web.Request) -> web.Response:
    store: ConfigStore = request.app["store"]
    engine: AutomationEngine = request.app["engine"]
    automation = store.get_automation(request.match_info["automation_id"])
    if not automation:
        raise web.HTTPNotFound(text=json.dumps({"error": "Automation not found"}), content_type="application/json")
    result = engine.start(automation, source="web interface")
    status = 202 if result.accepted else 409
    return web.json_response(
        {"accepted": result.accepted, "run_id": result.run_id, "reason": result.reason}, status=status
    )


async def get_logs(request: web.Request) -> web.Response:
    engine: AutomationEngine = request.app["engine"]
    try:
        after = int(request.query.get("after", "0"))
    except ValueError:
        after = 0
    return web.json_response({"logs": engine.logs_after(after)})


def _display_name(entity: dict[str, Any]) -> str:
    name = entity.get("name")
    if isinstance(name, str):
        return name
    if isinstance(name, dict):
        return str(name.get("en") or next(iter(name.values()), ""))
    return str(entity.get("entity_id", ""))
