"""Integration-API setup flow for Remote Core authentication."""

from __future__ import annotations

import logging
from typing import Any, Callable

import ucapi

from .config_store import ConfigStore
from .core_client import CoreClient
from .remote_auth import (
    RemoteAuthError,
    create_persistent_api_key,
    normalize_remote_address,
    setup_address_from_core_url,
)

_LOG = logging.getLogger(__name__)
SettingsChangedCallback = Callable[[], None]


class RemoteApiSetupFlow:
    """Collect a Remote PIN and exchange it for a persistent API key."""

    def __init__(
        self,
        store: ConfigStore,
        core: CoreClient,
        *,
        on_settings_changed: SettingsChangedCallback | None = None,
    ) -> None:
        self._store = store
        self._core = core
        self._on_settings_changed = on_settings_changed

    async def handle(self, message: ucapi.SetupDriver) -> ucapi.SetupAction:
        """Handle setup start, user input, and cancellation messages."""

        if isinstance(message, ucapi.DriverSetupRequest):
            # Support values embedded in a future static setup schema as well as
            # the current dynamic request page.
            if message.setup_data.get("remote_address") or message.setup_data.get("remote_password"):
                return await self._handle_input(message.setup_data)
            return self._input_page()

        if isinstance(message, ucapi.UserDataResponse):
            return await self._handle_input(message.input_values)

        if isinstance(message, ucapi.AbortDriverSetup):
            _LOG.info("Advanced Automations setup was aborted")
            return ucapi.SetupError(error_type=ucapi.IntegrationSetupError.OTHER)

        return ucapi.SetupError(error_type=ucapi.IntegrationSetupError.OTHER)

    def _input_page(self, error: str | None = None) -> ucapi.RequestUserInput:
        settings = self._store.settings()
        has_key = bool(settings.api_key)
        description = (
            "Enter the Remote address and the Web Configurator PIN shown on the Remote. "
            "Advanced Automations authenticates as `web-configurator`, creates a persistent "
            "admin API key, stores only that key, and immediately discards the PIN."
        )
        if has_key:
            description += (
                " Leave the PIN empty to keep the existing API key when the Remote address "
                "has not changed, or enter a PIN to create a replacement key."
            )

        page_settings: list[dict[str, Any]] = []
        if error:
            page_settings.append(
                {
                    "id": "error",
                    "label": {"en": "Setup could not be completed"},
                    "field": {"label": {"value": {"en": error}}},
                }
            )
        page_settings.extend(
            [
                {
                    "id": "info",
                    "label": {"en": "Remote API access"},
                    "field": {"label": {"value": {"en": description}}},
                },
                {
                    "id": "remote_address",
                    "label": {"en": "Remote IP address or hostname"},
                    "field": {
                        "text": {
                            "value": setup_address_from_core_url(settings.core_url),
                        }
                    },
                },
                {
                    "id": "remote_password",
                    "label": {
                        "en": (
                            "Web Configurator PIN (empty: keep existing key)"
                            if has_key
                            else "Web Configurator PIN"
                        )
                    },
                    "field": {"password": {}},
                },
            ]
        )
        return ucapi.RequestUserInput(
            title={"en": "Remote API authentication"},
            settings=page_settings,
        )

    async def _handle_input(self, values: dict[str, str]) -> ucapi.SetupAction:
        remote_address = str(values.get("remote_address") or "").strip()
        password = str(values.get("remote_password") or values.get("password") or "")
        current = self._store.settings()

        try:
            requested_endpoints = normalize_remote_address(remote_address)
        except RemoteAuthError as err:
            return self._input_page(str(err))

        api_key = current.api_key
        current_address_matches = False
        try:
            current_address_matches = (
                normalize_remote_address(current.core_url).websocket_url
                == requested_endpoints.websocket_url
            )
        except RemoteAuthError:
            pass

        if password.strip():
            try:
                endpoints, api_key = await create_persistent_api_key(
                    remote_address,
                    password,
                    timeout_seconds=current.request_timeout_seconds,
                )
            except RemoteAuthError as err:
                _LOG.warning(
                    "Remote persistent API-key creation failed: category=%s status=%s",
                    err.code,
                    err.status,
                )
                return self._input_page(str(err))
        elif not api_key or not current_address_matches:
            return self._input_page(
                "Enter the Web Configurator PIN to create an API key for this Remote."
            )
        else:
            endpoints = requested_endpoints

        updated = current.model_copy(
            update={
                "core_url": endpoints.websocket_url,
                "api_key": api_key,
            }
        )
        self._store.update_settings(updated)
        await self._core.close()
        if self._on_settings_changed is not None:
            try:
                self._on_settings_changed()
            except Exception:  # pragma: no cover - setup must remain completed after persistence
                _LOG.exception("Remote API settings were saved, but runtime reload failed")
        _LOG.info("Remote API access configured with a persistent API key")
        return ucapi.SetupComplete()

