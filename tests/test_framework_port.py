from __future__ import annotations

import unittest
from unittest.mock import patch

from ucapi_framework import BaseIntegrationDriver, RemoteEntity, SensorEntity

from uc_advanced_automations.core_client import rest_url_from_core_url
from uc_advanced_automations.framework_driver import AdvancedAutomationsDriver
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


class _FakeAuth:
    def __init__(self) -> None:
        self.names: list[str] = []

    async def generate_key(self, name: str) -> str:
        self.names.append(name)
        return "persistent-key"


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

    async def test_api_key_creation_is_delegated_to_unfurled_rotation(self) -> None:
        with patch("uc_advanced_automations.remote_auth.Remote", _FakeRemote):
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
        self.assertEqual(remote.auth.names, [API_KEY_NAME])
        self.assertTrue(remote.closed)


if __name__ == "__main__":
    unittest.main()
