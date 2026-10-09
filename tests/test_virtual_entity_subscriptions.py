# Advanced Automations v2.0.0
"""Regression tests for legacy virtual entity IDs and framework subscription events."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any

import ucapi

from uc_advanced_automations.framework_driver import AdvancedAutomationsDriver


class _EntityCollection:
    def __init__(self) -> None:
        self.items: dict[str, Any] = {}
        self.added: list[str] = []

    def contains(self, entity_id: str) -> bool:
        return entity_id in self.items

    def get(self, entity_id: str) -> Any | None:
        return self.items.get(entity_id)

    def add(self, entity: Any) -> None:
        self.items[entity.id] = entity
        self.added.append(entity.id)

    def remove(self, entity_id: str) -> None:
        self.items.pop(entity_id, None)


class _FakeIntegrationAPI:
    def __init__(self) -> None:
        self.available_entities = _EntityCollection()
        self.configured_entities = _EntityCollection()
        self.handlers: dict[Any, list[Any]] = {}

    def listens_to(self, event: Any) -> Any:
        def register(handler: Any) -> Any:
            self.handlers.setdefault(event, []).append(handler)
            return handler

        return register


class VirtualEntitySubscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.api = _FakeIntegrationAPI()
        self.api.available_entities.add(SimpleNamespace(id="advanced_automations"))
        self.api.available_entities.add(SimpleNamespace(id="last_automation_triggered"))
        self.driver = AdvancedAutomationsDriver(
            loop=asyncio.get_running_loop(), api=self.api
        )

    async def test_subscribe_legacy_ids_without_device_parsing(self) -> None:
        # Exercise the handler registered by the framework, not just the override.
        subscribe = self.api.handlers[ucapi.Events.SUBSCRIBE_ENTITIES][-1]
        await subscribe(
            ["advanced_automations", "last_automation_triggered", "unknown"]
        )
        self.assertTrue(self.api.configured_entities.contains("advanced_automations"))
        self.assertTrue(
            self.api.configured_entities.contains("last_automation_triggered")
        )
        self.assertFalse(self.api.configured_entities.contains("unknown"))
        self.assertEqual(self.driver._device_instances, {})

        # A Remote may re-send its subscriptions after reconnect; no duplicates.
        await subscribe(["advanced_automations", "last_automation_triggered"])
        self.assertEqual(
            self.api.configured_entities.added,
            ["advanced_automations", "last_automation_triggered"],
        )

    async def test_unsubscribe_removes_only_requested_virtual_entity(self) -> None:
        subscribe = self.api.handlers[ucapi.Events.SUBSCRIBE_ENTITIES][-1]
        unsubscribe = self.api.handlers[ucapi.Events.UNSUBSCRIBE_ENTITIES][-1]
        await subscribe(["advanced_automations", "last_automation_triggered"])
        await unsubscribe(["last_automation_triggered", "missing"])
        self.assertTrue(self.api.configured_entities.contains("advanced_automations"))
        self.assertFalse(
            self.api.configured_entities.contains("last_automation_triggered")
        )
        await unsubscribe(["last_automation_triggered"])  # idempotent
        await subscribe(["last_automation_triggered"])
        self.assertTrue(
            self.api.configured_entities.contains("last_automation_triggered")
        )


if __name__ == "__main__":
    unittest.main()
