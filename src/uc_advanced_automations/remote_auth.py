"""Remote REST API authentication helpers.

The Remote's short-lived web-configurator PIN is used only to create a
persistent API key. The PIN is never returned by this module and must never be
stored in application configuration.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import SplitResult, quote, urlsplit, urlunsplit

import aiohttp

WEB_CONFIGURATOR_USERNAME = "web-configurator"
API_KEY_NAME = "Advanced Automations"


class RemoteAuthErrorCode(StrEnum):
    """Stable failure categories used by the Integration-API setup flow."""

    INVALID_INPUT = "invalid_input"
    AUTHORIZATION = "authorization"
    NOT_FOUND = "not_found"
    CONNECTION = "connection"
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"


class RemoteAuthError(RuntimeError):
    """Persistent API-key creation failed."""

    def __init__(
        self,
        message: str,
        code: RemoteAuthErrorCode,
        *,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class RemoteEndpoints:
    """Normalized REST and WebSocket Core API endpoints for one Remote."""

    rest_base_url: str
    websocket_url: str


def _replace_scheme(parts: SplitResult, scheme: str, path: str) -> str:
    return urlunsplit((scheme, parts.netloc, path, "", ""))


def normalize_remote_address(value: str) -> RemoteEndpoints:
    """Normalize an IP, hostname, REST URL, or WebSocket URL.

    Examples accepted by the setup flow:

    - ``192.168.1.50``
    - ``remote.local``
    - ``http://192.168.1.50``
    - ``ws://192.168.1.50/ws``
    - ``https://remote.example``
    """

    candidate = str(value or "").strip()
    if not candidate:
        raise RemoteAuthError("Remote address is required", RemoteAuthErrorCode.INVALID_INPUT)

    if "://" not in candidate:
        candidate = f"http://{candidate}"

    try:
        parts = urlsplit(candidate)
        # Accessing ``port`` validates malformed host:port combinations.
        _ = parts.port
    except ValueError as err:
        raise RemoteAuthError("Remote address is invalid", RemoteAuthErrorCode.INVALID_INPUT) from err

    scheme = parts.scheme.lower()
    if scheme not in {"http", "https", "ws", "wss"}:
        raise RemoteAuthError(
            "Remote address must use http, https, ws, or wss",
            RemoteAuthErrorCode.INVALID_INPUT,
        )
    if not parts.hostname or not parts.netloc:
        raise RemoteAuthError("Remote address is invalid", RemoteAuthErrorCode.INVALID_INPUT)
    if parts.username or parts.password:
        raise RemoteAuthError(
            "Do not include credentials in the Remote address",
            RemoteAuthErrorCode.INVALID_INPUT,
        )
    if parts.query or parts.fragment:
        raise RemoteAuthError(
            "Remote address must not include a query or fragment",
            RemoteAuthErrorCode.INVALID_INPUT,
        )

    path = parts.path.rstrip("/")
    if path not in {"", "/ws", "/api"}:
        raise RemoteAuthError(
            "Remote address must not include an application path",
            RemoteAuthErrorCode.INVALID_INPUT,
        )

    secure = scheme in {"https", "wss"}
    rest_scheme = "https" if secure else "http"
    websocket_scheme = "wss" if secure else "ws"
    return RemoteEndpoints(
        rest_base_url=_replace_scheme(parts, rest_scheme, ""),
        websocket_url=_replace_scheme(parts, websocket_scheme, "/ws"),
    )


def setup_address_from_core_url(core_url: str) -> str:
    """Return a user-editable Remote address for the setup form."""

    try:
        endpoints = normalize_remote_address(core_url)
        parts = urlsplit(endpoints.rest_base_url)
    except RemoteAuthError:
        return "remote.local"
    if parts.scheme == "http":
        return parts.netloc
    return endpoints.rest_base_url


def _raise_api_key_http_error(status: int) -> None:
    if status in {401, 403}:
        raise RemoteAuthError(
            "The Web Configurator PIN was rejected",
            RemoteAuthErrorCode.AUTHORIZATION,
            status=status,
        )
    if status == 404:
        raise RemoteAuthError(
            "The Remote does not provide the API-key endpoint",
            RemoteAuthErrorCode.NOT_FOUND,
            status=status,
        )
    raise RemoteAuthError(
        f"Remote API-key creation failed with HTTP {status}",
        RemoteAuthErrorCode.INVALID_RESPONSE,
        status=status,
    )


async def _create_api_key_once(
    session: aiohttp.ClientSession,
    url: str,
    auth: aiohttp.BasicAuth,
    payload: dict[str, Any],
) -> tuple[int, str]:
    async with session.post(
        url,
        auth=auth,
        json=payload,
        headers={"Accept": "application/json"},
    ) as response:
        return response.status, await response.text()


async def _revoke_existing_api_key(
    session: aiohttp.ClientSession,
    url: str,
    auth: aiohttp.BasicAuth,
    key_name: str,
) -> bool:
    """Revoke an existing API key with ``key_name`` if one exists.

    The Core API returns HTTP 422 when creating a key whose name is already in
    use. API-key secrets cannot be retrieved again, so a setup retry must revoke
    the stale key before requesting a replacement.
    """

    async with session.get(
        url,
        auth=auth,
        params={"limit": 100},
        headers={"Accept": "application/json"},
    ) as response:
        body = await response.text()
        if response.status in {401, 403}:
            raise RemoteAuthError(
                "The Web Configurator PIN was rejected",
                RemoteAuthErrorCode.AUTHORIZATION,
                status=response.status,
            )
        if response.status == 404:
            return False
        if response.status < 200 or response.status >= 300:
            raise RemoteAuthError(
                f"Unable to inspect existing Remote API keys (HTTP {response.status})",
                RemoteAuthErrorCode.INVALID_RESPONSE,
                status=response.status,
            )

    try:
        data: Any = json.loads(body)
    except json.JSONDecodeError as err:
        raise RemoteAuthError(
            "The Remote returned an invalid API-key list",
            RemoteAuthErrorCode.INVALID_RESPONSE,
        ) from err

    if not isinstance(data, list):
        raise RemoteAuthError(
            "The Remote returned an invalid API-key list",
            RemoteAuthErrorCode.INVALID_RESPONSE,
        )

    key_id: str | None = None
    for item in data:
        if not isinstance(item, dict) or item.get("name") != key_name:
            continue
        candidate = item.get("key_id")
        if isinstance(candidate, str) and candidate.strip():
            key_id = candidate.strip()
            break

    if key_id is None:
        return False

    delete_url = f"{url}/{quote(key_id, safe='')}"
    async with session.delete(
        delete_url,
        auth=auth,
        headers={"Accept": "application/json"},
    ) as response:
        # A concurrent cleanup may have removed the key after the list request.
        if response.status == 404:
            return True
        if response.status in {401, 403}:
            raise RemoteAuthError(
                "The Web Configurator PIN was rejected",
                RemoteAuthErrorCode.AUTHORIZATION,
                status=response.status,
            )
        if response.status < 200 or response.status >= 300:
            raise RemoteAuthError(
                f"Unable to revoke the existing Remote API key (HTTP {response.status})",
                RemoteAuthErrorCode.INVALID_RESPONSE,
                status=response.status,
            )

    return True


async def create_persistent_api_key(
    remote_address: str,
    password: str,
    *,
    timeout_seconds: float = 10,
    key_name: str = API_KEY_NAME,
) -> tuple[RemoteEndpoints, str]:
    """Create and return a persistent Remote API key.

    The request follows the documented Remote REST API flow:
    ``POST /api/auth/api_keys`` using Basic Auth with username
    ``web-configurator`` and the PIN supplied by the user. The returned key is
    shown by the Remote only once, so callers must persist it immediately.

    If setup is retried after the Remote already created a key with the same
    name, the Core API responds with HTTP 422. In that case the stale key is
    revoked and creation is retried once.
    """

    endpoints = normalize_remote_address(remote_address)
    pin = str(password or "").strip()
    if not pin:
        raise RemoteAuthError(
            "Web Configurator PIN is required",
            RemoteAuthErrorCode.AUTHORIZATION,
        )

    url = f"{endpoints.rest_base_url}/api/auth/api_keys"
    timeout = aiohttp.ClientTimeout(total=max(1.0, float(timeout_seconds)))
    payload = {"name": key_name, "scopes": ["admin"]}
    auth = aiohttp.BasicAuth(WEB_CONFIGURATOR_USERNAME, pin)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            status, body = await _create_api_key_once(session, url, auth, payload)

            if status == 422:
                revoked = await _revoke_existing_api_key(session, url, auth, key_name)
                if revoked:
                    status, body = await _create_api_key_once(session, url, auth, payload)

            if status < 200 or status >= 300:
                _raise_api_key_http_error(status)

            try:
                data: Any = json.loads(body)
            except json.JSONDecodeError as err:
                raise RemoteAuthError(
                    "The Remote returned an invalid API-key response",
                    RemoteAuthErrorCode.INVALID_RESPONSE,
                    status=status,
                ) from err
            api_key = data.get("api_key") if isinstance(data, dict) else None
            if not isinstance(api_key, str) or not api_key.strip():
                raise RemoteAuthError(
                    "The Remote did not return an API key",
                    RemoteAuthErrorCode.INVALID_RESPONSE,
                    status=status,
                )
            return endpoints, api_key.strip()
    except RemoteAuthError:
        raise
    except asyncio.TimeoutError as err:
        raise RemoteAuthError(
            "The Remote did not respond before the request timed out",
            RemoteAuthErrorCode.TIMEOUT,
        ) from err
    except aiohttp.ClientConnectorError as err:
        raise RemoteAuthError(
            "Unable to connect to the Remote",
            RemoteAuthErrorCode.CONNECTION,
        ) from err
    except aiohttp.ClientError as err:
        raise RemoteAuthError(
            "Remote API communication failed",
            RemoteAuthErrorCode.CONNECTION,
        ) from err
