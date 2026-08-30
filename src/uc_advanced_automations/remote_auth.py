"""Remote authentication helpers backed by Unfurled."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import SplitResult, urlsplit, urlunsplit

from unfurled.helpers.exceptions import AuthenticationError, HTTPError, UnfurledError
from unfurled.remote import Remote

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
    """Normalize an IP, hostname, REST URL, or WebSocket URL."""

    candidate = str(value or "").strip()
    if not candidate:
        raise RemoteAuthError("Remote address is required", RemoteAuthErrorCode.INVALID_INPUT)
    if "://" not in candidate:
        candidate = f"http://{candidate}"

    try:
        parts = urlsplit(candidate)
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
    return RemoteEndpoints(
        rest_base_url=_replace_scheme(parts, "https" if secure else "http", ""),
        websocket_url=_replace_scheme(parts, "wss" if secure else "ws", "/ws"),
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


async def create_persistent_api_key(
    remote_address: str,
    password: str,
    *,
    timeout_seconds: float = 10,
    key_name: str = API_KEY_NAME,
) -> tuple[RemoteEndpoints, str]:
    """Create a persistent admin key through the PIN-safe Unfurled path.

    Web Configurator PIN authentication is intentionally kept to the same
    one-request flow used by the working Custom Select integration: create a new
    admin key directly and never attempt to enumerate or delete existing keys
    while authenticated with the PIN. ``Authentication.generate_key`` performs a
    GET/DELETE/POST rotation sequence, which is unnecessary during initial setup
    and can fail before the key has been created.

    A short random suffix avoids Core's duplicate-name 422 response without
    requiring any preflight key-list request. Only the returned API-key secret is
    persisted by Advanced Automations; the PIN and generated key name are not.
    """

    endpoints = normalize_remote_address(remote_address)
    pin = str(password or "").strip()
    if not pin:
        raise RemoteAuthError(
            "Web Configurator PIN is required",
            RemoteAuthErrorCode.AUTHORIZATION,
        )

    remote = Remote(
        f"{endpoints.rest_base_url}/api/",
        pin=pin,
        wake_if_asleep=False,
    )
    generated_name = f"{key_name} {secrets.token_hex(3)}"
    try:
        async with asyncio.timeout(max(1.0, float(timeout_seconds))):
            api_key = await remote.auth.create_key(generated_name)
        if not isinstance(api_key, str) or not api_key.strip():
            raise RemoteAuthError(
                "The Remote did not return an API key",
                RemoteAuthErrorCode.INVALID_RESPONSE,
            )
        return endpoints, api_key.strip()
    except RemoteAuthError:
        raise
    except TimeoutError as err:
        raise RemoteAuthError(
            "The Remote did not respond before the request timed out",
            RemoteAuthErrorCode.TIMEOUT,
        ) from err
    except AuthenticationError as err:
        raise RemoteAuthError(
            "The Web Configurator PIN was rejected",
            RemoteAuthErrorCode.AUTHORIZATION,
            status=401,
        ) from err
    except HTTPError as err:
        code = (
            RemoteAuthErrorCode.NOT_FOUND
            if err.status_code == 404
            else RemoteAuthErrorCode.INVALID_RESPONSE
        )
        raise RemoteAuthError(
            f"Remote API-key creation failed with HTTP {err.status_code}: {err.message}",
            code,
            status=err.status_code,
        ) from err
    except (UnfurledError, OSError) as err:
        raise RemoteAuthError(
            f"Unable to communicate with the Remote: {err}",
            RemoteAuthErrorCode.CONNECTION,
        ) from err
    finally:
        await remote.close()
