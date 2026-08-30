"""Remote authentication helpers backed by Unfurled."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import SplitResult, urlsplit, urlunsplit

from unfurled.api import CoreAPI
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
    """Create and verify a persistent admin key using the PIN-safe flow.

    This intentionally mirrors the working Custom Select integration. Web
    Configurator PIN authentication is used only for the direct key-creation
    request. ``Authentication.generate_key`` isn't used because it first lists
    and deletes existing keys before POSTing a replacement.

    A short random suffix avoids duplicate-name HTTP 422 responses without any
    preflight key-list request. The returned secret is then verified in a fresh
    bearer-authenticated CoreAPI session before setup is allowed to complete.
    Only the API-key secret is persisted; the PIN and generated key name are not.
    """

    endpoints = normalize_remote_address(remote_address)
    pin = str(password or "").strip()
    if not pin:
        raise RemoteAuthError(
            "Web Configurator PIN is required",
            RemoteAuthErrorCode.AUTHORIZATION,
        )

    rest_url = f"{endpoints.rest_base_url}/api/"
    remote = Remote(
        rest_url,
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
            api_key = api_key.strip()

            # Do not verify through ``remote.api``: that CoreAPI session was
            # created with Basic PIN auth. Open a fresh session so this request
            # proves the returned bearer key itself is valid, exactly like
            # Custom Select does before accepting its configuration.
            async with CoreAPI(
                rest_url,
                api_key=api_key,
                timeout=max(1.0, float(timeout_seconds)),
            ) as verification_api:
                await verification_api.get_system_info()

        return endpoints, api_key
    except RemoteAuthError:
        raise
    except TimeoutError as err:
        raise RemoteAuthError(
            "The Remote did not respond before the request timed out",
            RemoteAuthErrorCode.TIMEOUT,
        ) from err
    except AuthenticationError as err:
        raise RemoteAuthError(
            "The Web Configurator PIN or newly created API key was rejected",
            RemoteAuthErrorCode.AUTHORIZATION,
            status=401,
        ) from err
    except HTTPError as err:
        code = (
            RemoteAuthErrorCode.NOT_FOUND
            if err.status_code == 404
            else RemoteAuthErrorCode.AUTHORIZATION
            if err.status_code in {401, 403}
            else RemoteAuthErrorCode.INVALID_RESPONSE
        )
        raise RemoteAuthError(
            f"Remote API-key setup failed with HTTP {err.status_code}: {err.message}",
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
