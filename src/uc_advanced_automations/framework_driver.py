"""ucapi-framework driver for Advanced Automations.

Advanced Automations does not represent a conventional external AV device: its
entities are generated from the local automation database, while Unfurled talks
back to the hosting Remote Core.

On Remote hardware the Core gives custom integrations only a short cold-start
window. ``ucapi-framework`` currently has an eager package import surface, so the
Integration API socket is bootstrapped with ``ucapi`` first and this driver then
adopts that already-listening API instance. This preserves the framework's
lifecycle/event handling without making Core wait for the framework import.
"""

from __future__ import annotations

from typing import Any

import ucapi
from ucapi_framework import BaseDeviceInterface, BaseIntegrationDriver


class IntegrationRuntimeDevice(BaseDeviceInterface):
    """No-op framework device used as the integration runtime type."""

    @property
    def identifier(self) -> str:
        return "advanced_automations"

    @property
    def name(self) -> str:
        return "Advanced Automations"

    @property
    def address(self) -> str | None:
        return None

    @property
    def log_id(self) -> str:
        return "AdvancedAutomations"

    @property
    def is_connected(self) -> bool:
        return True

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None


class AdvancedAutomationsDriver(BaseIntegrationDriver[IntegrationRuntimeDevice, Any]):
    """Framework-backed Integration-API driver.

    ``api`` is optional for normal/external usage. When supplied, the framework
    adopts the already-created ``IntegrationAPI`` rather than constructing a
    second listener. The adopted path mirrors ``BaseIntegrationDriver.__init__``
    and deliberately calls the framework's own ``_setup_event_handlers``.
    """

    def __init__(self, *, loop=None, api: ucapi.IntegrationAPI | None = None) -> None:
        if api is None:
            super().__init__(
                device_class=IntegrationRuntimeDevice,
                entity_classes=[],
                loop=loop,
                driver_id="advanced_automations",
            )
            return

        self._loop = loop
        self.api = api
        self._device_class = IntegrationRuntimeDevice
        self._require_connection_before_registry = False
        self.driver_id = "advanced_automations"
        self._entity_classes = []
        self._device_instances = {}
        self._config_manager = None
        self.entity_id_separator = "."
        self._pending_setup_task = None
        self._setup_event_handlers()
