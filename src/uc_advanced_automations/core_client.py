"""Authenticated client for the Unfolded Circle Remote Core WebSocket API."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

_LOG = logging.getLogger(__name__)


class CoreApiError(RuntimeError):
    """Remote Core API request failed."""

    def __init__(self, message: str, code: int = 500) -> None:
        super().__init__(message)
        self.code = code


class CoreClient:
    """Small reconnecting Core API client with concurrent request routing."""

    def __init__(self, settings_provider: Callable[[], Any]) -> None:
        self._settings_provider = settings_provider
        self._ws: ClientConnection | None = None
        self._receiver: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._connect_lock = asyncio.Lock()
        self._request_id = 0
        self._connected = asyncio.Event()
        self._last_error: str | None = None

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and self._connected.is_set()

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def connect(self, force: bool = False) -> None:
        async with self._connect_lock:
            if force:
                await self.close()
            if self._ws is not None and self._connected.is_set():
                return

            settings = self._settings_provider()
            if not settings.api_key:
                raise CoreApiError("Remote Core API key is not configured", 401)

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
                _LOG.info("Connected to Remote Core API at %s", settings.core_url)
            except Exception as err:
                self._last_error = str(err)
                self._connected.clear()
                self._ws = None
                raise CoreApiError(f"Unable to connect to Remote Core API: {err}", 503) from err

    async def close(self) -> None:
        receiver = self._receiver
        self._receiver = None
        if receiver and receiver is not asyncio.current_task():
            receiver.cancel()
            try:
                await receiver
            except asyncio.CancelledError:
                pass

        ws = self._ws
        self._ws = None
        self._connected.clear()
        if ws:
            try:
                await ws.close()
            except Exception:
                pass
        self._fail_pending(CoreApiError("Core API connection closed", 503))

    async def request(self, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        await self.connect()
        settings = self._settings_provider()
        self._request_id += 1
        request_id = self._request_id
        payload: dict[str, Any] = {"kind": "req", "id": request_id, "msg": message}
        if data:
            payload["msg_data"] = data

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future

        try:
            assert self._ws is not None
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
        return response.get("msg_data") or {}

    async def get_entity(self, entity_id: str) -> dict[str, Any]:
        return await self.request("get_entity", {"entity_id": entity_id})

    async def get_entities(self) -> list[dict[str, Any]]:
        entities: list[dict[str, Any]] = []
        page = 1
        limit = 100
        while True:
            data = await self.request("get_entities", {"paging": {"limit": limit, "page": page}})
            batch = data.get("entities", [])
            if not isinstance(batch, list):
                raise CoreApiError("Core API returned an invalid entity list")
            entities.extend(batch)
            paging = data.get("paging", {})
            count = int(paging.get("count", len(entities)))
            if len(entities) >= count or len(batch) < limit:
                break
            page += 1
        return entities

    async def execute_entity_command(
        self,
        entity_id: str,
        command_id: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"entity_id": entity_id, "cmd_id": command_id}
        if params:
            data["params"] = params
        return await self.request("execute_entity_command", data)

    async def test_connection(self) -> dict[str, Any]:
        await self.connect(force=True)
        entities = await self.get_entities()
        return {"connected": True, "entity_count": len(entities)}

    async def _receive_loop(self) -> None:
        try:
            assert self._ws is not None
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
                    _LOG.debug("Core event: %s", message.get("msg"))
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._last_error = str(err)
            _LOG.warning("Core API receiver stopped: %s", err)
        finally:
            self._connected.clear()
            self._ws = None
            self._fail_pending(CoreApiError("Core API connection stopped", 503))

    def _fail_pending(self, error: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
