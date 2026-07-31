"""Authenticated client for the Remote WebSocket API."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

_LOG = logging.getLogger(__name__)
EventCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class CoreApiError(RuntimeError):
    """Remote API request failed."""

    def __init__(self, message: str, code: int = 500) -> None:
        super().__init__(message)
        self.code = code


class CoreClient:
    """Reconnectable Core API client with request routing and event subscriptions."""

    def __init__(self, settings_provider: Callable[[], Any]) -> None:
        self._settings_provider = settings_provider
        self._ws: ClientConnection | None = None
        self._receiver: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._connect_lock = asyncio.Lock()
        self._request_id = 0
        self._connected = asyncio.Event()
        self._last_error: str | None = None
        self._event_listeners: dict[str, list[EventCallback]] = {}
        self._command_metadata: list[dict[str, Any]] | None = None
        self._ever_connected = False

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and self._connected.is_set()

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
        newly_connected = False
        async with self._connect_lock:
            if force:
                await self.close()
            if self._ws is not None and self._connected.is_set():
                return

            settings = self._settings_provider()
            if not settings.api_key:
                raise CoreApiError("Remote API key is not configured", 401)

            try:
                self._ws = await websockets.connect(
                    settings.core_url,
                    additional_headers={"API-KEY": settings.api_key},
                    open_timeout=settings.request_timeout_seconds,
                    close_timeout=3,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=4 * 1024 * 1024,
                )
                self._connected.set()
                self._last_error = None
                self._receiver = asyncio.create_task(self._receive_loop(), name="uc-core-receiver")
                newly_connected = True
                _LOG.info("Connected to Remote API at %s", settings.core_url)
            except Exception as err:
                self._last_error = str(err)
                self._connected.clear()
                self._ws = None
                raise CoreApiError(f"Unable to connect to Remote API: {err}", 503) from err

        if newly_connected:
            try:
                # The `entities` channel covers entity and activity-group events.
                await self._request_connected("subscribe_events", {"channels": ["entities"]})
                first_connection = not self._ever_connected
                self._ever_connected = True
                await self._dispatch_event("connection", {"event": "connected", "first_connection": first_connection})
            except Exception:
                await self.close()
                raise

    async def close(self) -> None:
        # Capture the socket before cancelling the receiver: its finally block also
        # clears self._ws, which would otherwise lose the handle before close().
        ws = self._ws
        self._ws = None
        self._connected.clear()
        receiver = self._receiver
        self._receiver = None
        if receiver and receiver is not asyncio.current_task():
            receiver.cancel()
            try:
                await receiver
            except asyncio.CancelledError:
                pass

        if ws:
            try:
                await ws.close()
            except Exception:
                pass
        self._fail_pending(CoreApiError("Core API connection closed", 503))

    async def request(self, message: str, data: dict[str, Any] | None = None) -> Any:
        await self.connect()
        return await self._request_connected(message, data)

    async def _request_connected(self, message: str, data: dict[str, Any] | None = None) -> Any:
        settings = self._settings_provider()
        self._request_id += 1
        request_id = self._request_id
        payload: dict[str, Any] = {"kind": "req", "id": request_id, "msg": message}
        if data is not None:
            payload["msg_data"] = data

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future

        try:
            if self._ws is None:
                raise CoreApiError("Core API is not connected", 503)
            await self._ws.send(json.dumps(payload))
            response = await asyncio.wait_for(future, timeout=settings.request_timeout_seconds)
        except (websockets.ConnectionClosed, OSError) as err:
            self._pending.pop(request_id, None)
            await self.close()
            raise CoreApiError(f"Core API connection lost: {err}", 503) from err
        except asyncio.TimeoutError as err:
            self._pending.pop(request_id, None)
            raise CoreApiError(f"Core API request timed out: {message}", 408) from err

        code = int(response.get("code", 500))
        if code < 200 or code >= 300:
            details = response.get("msg_data") or response.get("msg") or "Unknown error"
            raise CoreApiError(f"Core API returned {code}: {details}", code)
        return response.get("msg_data")

    async def get_entity(self, entity_id: str) -> dict[str, Any]:
        data = await self.request("get_entity", {"entity_id": entity_id})
        if not isinstance(data, dict):
            raise CoreApiError("Core API returned invalid entity data")
        return data

    async def get_entities(self) -> list[dict[str, Any]]:
        entities: list[dict[str, Any]] = []
        page = 1
        limit = 100
        while True:
            data = await self.request("get_entities", {"paging": {"limit": limit, "page": page}})
            if not isinstance(data, dict):
                raise CoreApiError("Core API returned an invalid entity response")
            batch = data.get("entities", [])
            if not isinstance(batch, list):
                raise CoreApiError("Core API returned an invalid entity list")
            entities.extend(item for item in batch if isinstance(item, dict))
            paging = data.get("paging", {})
            count = int(paging.get("count", len(entities))) if isinstance(paging, dict) else len(entities)
            if len(entities) >= count or len(batch) < limit:
                break
            page += 1
        return entities

    async def get_entity_commands(self, entity_id: str) -> list[str]:
        data = await self.request("get_entity_commands", {"entity_id": entity_id})
        raw_commands: Any
        if isinstance(data, dict):
            raw_commands = data.get("commands", [])
        else:
            raw_commands = data
        if not isinstance(raw_commands, list):
            raise CoreApiError("Core API returned invalid entity commands")
        commands: list[str] = []
        for item in raw_commands:
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
        data = await self.request("get_entity_command_metadata")
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
                by_id.get(
                    command_id,
                    {"id": command_id, "cmd_id": command_id, "name": {"en": command_id}},
                )
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
        data: dict[str, Any] = {"entity_id": entity_id, "cmd_id": command_id}
        if params:
            data["params"] = params
        result = await self.request("execute_entity_command", data)
        return result if isinstance(result, dict) else {}

    async def refresh_available_entities(self, integration_id: str) -> dict[str, Any]:
        data = await self.request(
            "get_available_entities",
            {
                "force_reload": True,
                "filter": {"integration_id": integration_id, "all": True},
                "paging": {"limit": 100, "page": 1},
            },
        )
        return data if isinstance(data, dict) else {}

    async def integration_command(self, integration_id: str, command: str) -> None:
        await self.request(
            "integration_cmd",
            {"integration_id": integration_id, "cmd_id": command.upper()},
        )

    async def test_connection(self) -> dict[str, Any]:
        await self.connect(force=True)
        entities = await self.get_entities()
        metadata = await self.get_entity_command_metadata(force=True)
        return {
            "connected": True,
            "entity_count": len(entities),
            "command_metadata_count": len(metadata),
            "event_subscription": "entities",
        }

    async def _receive_loop(self) -> None:
        try:
            if self._ws is None:
                return
            async for raw in self._ws:
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    _LOG.warning("Ignoring invalid Core API JSON")
                    continue

                if message.get("kind") == "resp":
                    request_id = message.get("req_id")
                    future = self._pending.pop(request_id, None)
                    if future and not future.done():
                        future.set_result(message)
                elif message.get("kind") == "event":
                    await self._dispatch_event(str(message.get("msg", "")), message.get("msg_data") or {})
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._last_error = str(err)
            _LOG.warning("Core API receiver stopped: %s", err)
        finally:
            was_connected = self._connected.is_set()
            self._connected.clear()
            self._ws = None
            self._fail_pending(CoreApiError("Core API connection stopped", 503))
            if was_connected:
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
