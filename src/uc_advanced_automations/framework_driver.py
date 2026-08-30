"""ucapi-framework driver for Advanced Automations.

Advanced Automations does not represent a conventional external AV device: its
entities are generated from the local automation database, while Unfurled talks
back to the hosting Remote Core.  The framework driver therefore owns the
Integration-API lifecycle and event wiring; domain entities continue to be
rebuilt by :class:`IntegrationController` when automation definitions change.
"""

from __future__ import annotations

from typing import Any

from ucapi_framework import BaseDeviceInterface, BaseIntegrationDriver


class IntegrationRuntimeDevice(BaseDeviceInterface):
    """No-op framework device used as the integration runtime type.

    No instances are registered: the actual Remote Core transport is provided by
    Unfurled through ``CoreClient``.  A concrete device type is still supplied to
    ``BaseIntegrationDriver`` so the driver remains fully framework-native.
    """

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
    """Framework-backed Integration-API driver."""

    def __init__(self, *, loop=None) -> None:
        super().__init__(
            device_class=IntegrationRuntimeDevice,
            entity_classes=[],
            loop=loop,
            driver_id="advanced-automations",
        )
