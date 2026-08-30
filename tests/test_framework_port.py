from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import ucapi
from ucapi_framework import BaseIntegrationDriver, RemoteEntity, SensorEntity

from uc_advanced_automations.core_client import rest_url_from_core_url
from uc_advanced_automations.framework_driver import AdvancedAutomationsDriver
from uc_advanced_automations.main import _DeferredSetupHandler, _restore_early_subscriptions
from uc_advanced_automations.remote_auth import (
    API_KEY_NAME,
    create_persistent_api_key,
    normalize_remote_address,
)


class FrameworkPortTests(unittest.TestCase):
    def test_driver_is_framework_native(self) -> None:
        self.assertTrue(issubclass(AdvancedAutomationsDriver, BaseIntegrationDriver))

    def test_framework_entity_classes_are_available(self) -> None:
        self.assertTrue(issubclass(RemoteEntity, object))
        self.assertTrue(issubclass(SensorEntity, object))

    def test_framework_driver_can_adopt_bootstrap_api(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            api = ucapi.IntegrationAPI(loop)
            driver = AdvancedAutomationsDriver(loop=loop, api=api)
            self.assertIs(driver.api, api)
            self.assertEqual(driver.driver_id, "advanced_automations")
        finally:
            loop.close()

    def test_core_url_conversion_accepts_persisted_websocket_url(self) -> None:
        self.assertEqual(
            rest_url_from_core_url("ws://192.168.1.50/ws"),
            "http://192.168.1.50/api/",
        )
        self.assertEqual(
            rest_url_from_core_url("wss://remote.example/ws"),
            "https://remote.example/api/",
        )

    def test_remote_address_normalization_preserves_port(self) -> None:
        endpoints = normalize_remote_address("http://192.168.1.50:8080/api")
        self.assertEqual(endpoints.rest_base_url, "http://192.168.1.50:8080")
        self.assertEqual(endpoints.websocket_url, "ws://192.168.1.50:8080/ws")


class BootstrapStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_request_activates_runtime_before_delegate_is_ready(self) -> None:
        activated = asyncio.Event()
        handler = _DeferredSetupHandler(activated.set)

        task = asyncio.create_task(handler(object()))
        await asyncio.wait_for(activated.wait(), timeout=0.2)
        self.assertFalse(task.done())

        async def delegate(message: object) -> object:
            return message

        handler.set_delegate(delegate)
        result = await asyncio.wait_for(task, timeout=0.2)
        self.assertIsInstance(result, object)

    def test_early_subscriptions_are_replayed_after_entities_exist(self) -> None:
        class Collection:
            def __init__(self, values: dict[str, object] | None = None) -> None:
                self.values = dict(values or {})

            def contains(self, entity_id: str) -> bool:
                return entity_id in self.values

            def get(self, entity_id: str) -> object | None:
                return self.values.get(entity_id)

            def add(self, entity: object) -> None:
                self.values[getattr(entity, "id")] = entity

        class Entity:
            def __init__(self, entity_id: str) -> None:
                self.id = entity_id

        class Api:
            available_entities = Collection({"advanced_automations": Entity("advanced_automations")})
            configured_entities = Collection()

        restored = _restore_early_subscriptions(
            Api(),
            {"advanced_automations", "not_available_yet"},
        )
        self.assertEqual(restored, ["advanced_automations"])


class _FakeAuth:
    def __init__(self) -> None:
        self.created_names: list[str] = []

    async def create_key(self, name: str) -> str:
        self.created_names.append(name)
        return "persistent-key"

    async def generate_key(self, _name: str) -> str:
        raise AssertionError("PIN setup must not enumerate/rotate existing API keys")


class _FakeRemote:
    instances: list["_FakeRemote"] = []

    def __init__(self, endpoint: str, *, pin: str, wake_if_asleep: bool) -> None:
        self.endpoint = endpoint
        self.pin = pin
        self.wake_if_asleep = wake_if_asleep
        self.auth = _FakeAuth()
        self.closed = False
        self.__class__.instances.append(self)

    async def close(self) -> None:
        self.closed = True


class RemoteAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        _FakeRemote.instances.clear()

    async def test_api_key_creation_matches_custom_select_one_shot_pin_flow(self) -> None:
        with (
            patch("uc_advanced_automations.remote_auth.Remote", _FakeRemote),
            patch("uc_advanced_automations.remote_auth.secrets.token_hex", return_value="a1b2c3"),
        ):
            endpoints, api_key = await create_persistent_api_key(
                "192.168.1.50",
                "1234",
            )

        self.assertEqual(endpoints.websocket_url, "ws://192.168.1.50/ws")
        self.assertEqual(api_key, "persistent-key")
        self.assertEqual(len(_FakeRemote.instances), 1)
        remote = _FakeRemote.instances[0]
        self.assertEqual(remote.endpoint, "http://192.168.1.50/api/")
        self.assertEqual(remote.pin, "1234")
        self.assertEqual(remote.auth.created_names, [f"{API_KEY_NAME} a1b2c3"])
        self.assertTrue(remote.closed)


if __name__ == "__main__":
    unittest.main()
