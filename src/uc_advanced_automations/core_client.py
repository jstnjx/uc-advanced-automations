# Advanced Automations v2.0.0
"""Remote Core API adapter backed by Unfurled.

The application keeps a small compatibility surface for the automation engine and
web editor, while Unfurled owns HTTP authentication, REST calls and the reconnecting
Remote WebSocket transport.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from unfurled.api import CoreAPI, IntegrationInstanceCommand
from unfurled.helpers.exceptions import AuthenticationError, HTTPError, UnfurledError
from unfurled.helpers.websocket import RemoteWebSocketClient

_LOG = logging.getLogger(__name__)
EventCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class CoreApiError(RuntimeError):
    """Remote API request failed."""

    def __init__(self, message: str, code: int = 500) -> None:
        super().__init__(message)
        self.code = code


def rest_url_from_core_url(value: str) -> str:
    """Convert a persisted HTTP/WS Remote address to Unfurled's ``/api/`` URL."""

    candidate = str(value or "").strip()
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    parts = urlsplit(candidate)
    scheme = {"ws": "http", "wss": "https"}.get(parts.scheme.lower(), parts.scheme.lower())
    if scheme not in {"http", "https"} or not parts.netloc:
        raise CoreApiError("Remote address is invalid", 400)
    return urlunsplit((scheme, parts.netloc, "/api/", "", ""))


class CoreClient:
    """Compatibility client using Unfurled for the Remote Core transport."""

    def __init__(self, settings_provider: Callable[[], Any]) -> None:
        self._settings_provider = settings_provider
        self._api: CoreAPI | None = None
        self._ws: RemoteWebSocketClient | None = None
        self._connect_lock = asyncio.Lock()
        self._event_listeners: dict[str, list[EventCallback]] = {}
        self._command_metadata: list[dict[str, Any]] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._request_id = 100
        self._last_error: str | None = None
        self._ever_connected = False
        self._identity: tuple[str, str] | None = None

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and self._ws.is_connected

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def add_event_listener(self, message: str, callback: EventCallback) -> None:
        listeners = self._event_listeners.setdefault(message, [])
        if callback not in listeners:
            listeners.append(callback)

    def remove_event_listener(self, message: str, callback: EventCallback) -> None:
        listeners = self._event_listeners.get(message, [])
        if callback in listeners:
            listeners.remove(callback)

    async def connect(self, force: bool = False) -> None:
        async with self._connect_lock:
            settings = self._settings_provider()
            if not settings.api_key:
                raise CoreApiError("Remote API key is not configured", 401)

            rest_url = rest_url_from_core_url(settings.core_url)
            identity = (rest_url, settings.api_key)
            if force or (self._identity is not None and self._identity != identity):
                await self._close_unlocked()
            if self._api is not None and self._ws is not None:
                return

            try:
                self._api = CoreAPI(
                    rest_url,
                    api_key=settings.api_key,
                    timeout=settings.request_timeout_seconds,
                )
                # Verify credentials/reachability before reporting a usable connection.
                await self._api.get_entities(limit=10, page=1)

                self._ws = RemoteWebSocketClient(rest_url, settings.api_key, reconnect_delay=2.0)
                self._ws.on_message(self._on_ws_message)
                self._ws.on_connect(self._on_ws_reconnect)
                self._ws.on_disconnect(self._on_ws_disconnect)
                await self._ws.connect()
                self._identity = identity
                self._last_error = None

                first_connection = not self._ever_connected
                self._ever_connected = True
                await self._dispatch_event(
                    "connection",
                    {"event": "connected", "first_connection": first_connection},
                )
                _LOG.info("Connected to Remote Core through Unfurled at %s", rest_url)
            except Exception as err:
                self._last_error = str(err)
                await self._close_unlocked()
                raise self._map_error(err, "Unable to connect to Remote API") from err

    async def close(self) -> None:
        async with self._connect_lock:
            await self._close_unlocked()

    async def _close_unlocked(self) -> None:
        ws, api = self._ws, self._api
        self._ws = None
        self._api = None
        self._identity = None
        if ws is not None:
            try:
                await ws.disconnect()
            except Exception:
                pass
        if api is not None:
            try:
                await api.close()
            except Exception:
                pass
        self._fail_pending(CoreApiError("Core API connection closed", 503))

    async def _ensure_api(self) -> CoreAPI:
        await self.connect()
        if self._api is None:
            raise CoreApiError("Remote API is unavailable", 503)
        return self._api

    async def _ws_request(self, message: str, data: dict[str, Any] | None = None) -> Any:
        await self.connect()
        settings = self._settings_provider()
        ws = self._ws
        if ws is None:
            raise CoreApiError("Remote WebSocket is unavailable", 503)

        deadline = asyncio.get_running_loop().time() + settings.request_timeout_seconds
        while not ws.is_connected:
            if asyncio.get_running_loop().time() >= deadline:
                raise CoreApiError("Remote WebSocket connection timed out", 408)
            await asyncio.sleep(0.05)

        self._request_id += 1
        request_id = self._request_id
        payload: dict[str, Any] = {"kind": "req", "id": request_id, "msg": message}
        if data is not None:
            payload["msg_data"] = data
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await ws.send(payload)
            response = await asyncio.wait_for(future, timeout=settings.request_timeout_seconds)
        except asyncio.TimeoutError as err:
            raise CoreApiError(f"Core API request timed out: {message}", 408) from err
        finally:
            self._pending.pop(request_id, None)

        code = int(response.get("code", 500))
        if code < 200 or code >= 300:
            details = response.get("msg_data") or response.get("msg") or "Unknown error"
            raise CoreApiError(f"Core API returned {code}: {details}", code)
        return response.get("msg_data")

    async def get_entity(self, entity_id: str) -> dict[str, Any]:
        try:
            return await (await self._ensure_api()).get_entity(entity_id)
        except Exception as err:
            raise self._map_error(err, "Unable to get entity") from err

    async def get_entities(self) -> list[dict[str, Any]]:
        api = await self._ensure_api()
        entities: list[dict[str, Any]] = []
        try:
            page = 1
            while True:
                batch = await api.get_entities(limit=100, page=page)
                entities.extend(item for item in batch if isinstance(item, dict))
                if len(batch) < 100:
                    break
                page += 1
            return entities
        except Exception as err:
            raise self._map_error(err, "Unable to list entities") from err

    async def get_entity_commands(self, entity_id: str) -> list[str]:
        data = await self._ws_request("get_entity_commands", {"entity_id": entity_id})
        raw = data.get("commands", []) if isinstance(data, dict) else data
        if not isinstance(raw, list):
            raise CoreApiError("Core API returned invalid entity commands")
        commands: list[str] = []
        for item in raw:
            if isinstance(item, str):
                commands.append(item)
            elif isinstance(item, dict):
                value = item.get("id") or item.get("cmd_id")
                if isinstance(value, str):
                    commands.append(value)
        return commands

    async def get_entity_command_metadata(self, force: bool = False) -> list[dict[str, Any]]:
        if self._command_metadata is not None and not force:
            return [dict(item) for item in self._command_metadata]
        data = await self._ws_request("get_entity_command_metadata")
        if isinstance(data, dict):
            data = data.get("commands") or data.get("metadata")
        if not isinstance(data, list):
            raise CoreApiError("Core API returned invalid command metadata")
        self._command_metadata = [item for item in data if isinstance(item, dict)]
        return [dict(item) for item in self._command_metadata]

    async def get_command_definitions(self, entity_id: str) -> dict[str, Any]:
        entity, command_ids, metadata = await asyncio.gather(
            self.get_entity(entity_id),
            self.get_entity_commands(entity_id),
            self.get_entity_command_metadata(),
        )
        by_id = {item.get("id"): item for item in metadata if isinstance(item.get("id"), str)}
        commands: list[dict[str, Any]] = []
        for command_id in command_ids:
            command = dict(
                by_id.get(command_id, {"id": command_id, "cmd_id": command_id, "name": {"en": command_id}})
            )
            params = []
            for raw_param in command.get("params", []) or []:
                if not isinstance(raw_param, dict):
                    continue
                param = dict(raw_param)
                if param.get("type") == "selection":
                    items = param.get("items")
                    if isinstance(items, dict):
                        source = entity.get(items.get("source"), {})
                        values = source.get(items.get("field"), []) if isinstance(source, dict) else []
                        if isinstance(values, list):
                            param["values"] = values
                params.append(param)
            command["params"] = params
            commands.append(command)
        return {"entity": entity, "commands": commands}

    async def execute_entity_command(
        self,
        entity_id: str,
        command_id: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            result = await (await self._ensure_api()).put_entity_command(entity_id, command_id, params)
            return result if isinstance(result, dict) else {}
        except Exception as err:
            raise self._map_error(err, "Unable to execute entity command") from err

    async def refresh_available_entities(self, integration_id: str) -> dict[str, Any]:
        try:
            entities = await (await self._ensure_api()).get_integration_entities(
                integration_id, reload=True, limit=100
            )
            return {"entities": entities}
        except Exception as err:
            raise self._map_error(err, "Unable to refresh integration entities") from err

    async def integration_command(self, integration_id: str, command: str) -> None:
        try:
            cmd = IntegrationInstanceCommand(command.upper())
            await (await self._ensure_api()).put_integration(integration_id, cmd)
        except ValueError as err:
            raise CoreApiError(f"Unsupported integration command: {command}", 400) from err
        except Exception as err:
            raise self._map_error(err, "Unable to control integration") from err

    async def test_connection(self) -> dict[str, Any]:
        await self.connect(force=True)
        entities = await self.get_entities()
        metadata = await self.get_entity_command_metadata(force=True)
        return {
            "connected": True,
            "transport": "unfurled",
            "entity_count": len(entities),
            "command_metadata_count": len(metadata),
            "event_subscription": "all",
        }

    async def _on_ws_message(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            _LOG.warning("Ignoring invalid Remote WebSocket JSON")
            return

        if message.get("kind") == "resp":
            request_id = message.get("req_id")
            future = self._pending.get(request_id)
            if future and not future.done():
                future.set_result(message)
            return
        if message.get("kind") == "event":
            data = message.get("msg_data")
            await self._dispatch_event(
                str(message.get("msg", "")),
                data if isinstance(data, dict) else {},
            )

    async def _on_ws_reconnect(self) -> None:
        await self._dispatch_event("connection", {"event": "connected", "first_connection": False})

    async def _on_ws_disconnect(self) -> None:
        await self._dispatch_event("connection", {"event": "disconnected", "first_connection": False})

    async def _dispatch_event(self, message: str, data: dict[str, Any]) -> None:
        _LOG.debug("Core event: %s", message)
        callbacks = [*self._event_listeners.get(message, []), *self._event_listeners.get("*", [])]
        for callback in callbacks:
            try:
                result = callback(data)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                _LOG.exception("Core event listener failed for %s", message)

    def _fail_pending(self, error: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    @staticmethod
    def _map_error(error: Exception, prefix: str) -> CoreApiError:
        if isinstance(error, CoreApiError):
            return error
        if isinstance(error, AuthenticationError):
            return CoreApiError(f"{prefix}: authentication failed", 401)
        if isinstance(error, HTTPError):
            return CoreApiError(f"{prefix}: {error}", error.status_code)
        if isinstance(error, (UnfurledError, OSError)):
            return CoreApiError(f"{prefix}: {error}", 503)
        return CoreApiError(f"{prefix}: {error}", 500)
