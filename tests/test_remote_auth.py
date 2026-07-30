from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp
import ucapi
from aiohttp import web

from uc_advanced_automations.config_store import ConfigStore
from uc_advanced_automations.remote_auth import (
    API_KEY_NAME,
    RemoteAuthError,
    RemoteAuthErrorCode,
    create_persistent_api_key,
    normalize_remote_address,
    setup_address_from_core_url,
)
from uc_advanced_automations.setup_flow import RemoteApiSetupFlow


class RemoteAddressTests(unittest.TestCase):
    def test_normalizes_host_and_websocket_url(self):
        endpoints = normalize_remote_address("192.168.1.50")
        self.assertEqual(endpoints.rest_base_url, "http://192.168.1.50")
        self.assertEqual(endpoints.websocket_url, "ws://192.168.1.50/ws")

    def test_normalizes_secure_websocket_url(self):
        endpoints = normalize_remote_address("wss://remote.example/ws")
        self.assertEqual(endpoints.rest_base_url, "https://remote.example")
        self.assertEqual(endpoints.websocket_url, "wss://remote.example/ws")
        self.assertEqual(setup_address_from_core_url(endpoints.websocket_url), "https://remote.example")

    def test_rejects_credentials_in_address(self):
        with self.assertRaises(RemoteAuthError) as raised:
            normalize_remote_address("http://user:pass@remote.local")
        self.assertEqual(raised.exception.code, RemoteAuthErrorCode.INVALID_INPUT)


class ApiKeyCreationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.app = web.Application()
        self.app.router.add_post("/api/auth/api_keys", self._create_key)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        self.port = self.site._server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def _create_key(self, request: web.Request) -> web.Response:
        auth = aiohttp.BasicAuth.decode(request.headers["Authorization"])
        payload = await request.json()
        self.requests.append((auth, payload))
        if auth.login != "web-configurator" or auth.password != "1234":
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response(
            {
                "name": payload["name"],
                "api_key": "persistent-test-key",
                "active": True,
                "scopes": payload["scopes"],
            },
            status=201,
        )

    async def test_uses_documented_basic_auth_and_admin_scope(self):
        endpoints, key = await create_persistent_api_key(
            f"127.0.0.1:{self.port}",
            "1234",
        )
        self.assertEqual(key, "persistent-test-key")
        self.assertEqual(endpoints.websocket_url, f"ws://127.0.0.1:{self.port}/ws")
        auth, payload = self.requests[0]
        self.assertEqual(auth.login, "web-configurator")
        self.assertEqual(auth.password, "1234")
        self.assertEqual(payload, {"name": API_KEY_NAME, "scopes": ["admin"]})

    async def test_rejected_pin_is_authorization_error(self):
        with self.assertRaises(RemoteAuthError) as raised:
            await create_persistent_api_key(f"127.0.0.1:{self.port}", "wrong")
        self.assertEqual(raised.exception.code, RemoteAuthErrorCode.AUTHORIZATION)
        self.assertEqual(raised.exception.status, 401)


class SetupFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_requests_address_and_password(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory))

            class Core:
                async def close(self):
                    pass

            flow = RemoteApiSetupFlow(store, Core())
            action = await flow.handle(ucapi.DriverSetupRequest(False, {}))
            self.assertIsInstance(action, ucapi.RequestUserInput)
            fields = {item["id"]: item["field"] for item in action.settings}
            self.assertIn("remote_address", fields)
            self.assertIn("password", fields["remote_password"])

    async def test_setup_persists_only_returned_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory))

            class Core:
                closed = 0

                async def close(self):
                    self.closed += 1

            core = Core()
            callback = unittest.mock.Mock()
            flow = RemoteApiSetupFlow(store, core, on_settings_changed=callback)
            endpoints = normalize_remote_address("192.168.1.77")
            with patch(
                "uc_advanced_automations.setup_flow.create_persistent_api_key",
                new=AsyncMock(return_value=(endpoints, "created-persistent-key")),
            ) as create_key:
                action = await flow.handle(
                    ucapi.UserDataResponse(
                        {
                            "remote_address": "192.168.1.77",
                            "remote_password": "temporary-pin",
                        }
                    )
                )

            self.assertIsInstance(action, ucapi.SetupComplete)
            create_key.assert_awaited_once_with(
                "192.168.1.77",
                "temporary-pin",
                timeout_seconds=10.0,
            )
            self.assertEqual(store.settings().core_url, "ws://192.168.1.77/ws")
            self.assertEqual(store.settings().api_key, "created-persistent-key")
            persisted = (Path(directory) / "config.json").read_text(encoding="utf-8")
            self.assertNotIn("temporary-pin", persisted)
            self.assertIn("created-persistent-key", persisted)
            self.assertEqual(core.closed, 1)
            callback.assert_called_once_with()

    async def test_existing_key_can_be_kept_without_pin_for_same_remote(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory))
            current = store.settings().model_copy(
                update={"core_url": "ws://remote.local/ws", "api_key": "existing-key"}
            )
            store.update_settings(current)

            class Core:
                async def close(self):
                    pass

            flow = RemoteApiSetupFlow(store, Core())
            action = await flow.handle(
                ucapi.UserDataResponse(
                    {"remote_address": "remote.local", "remote_password": ""}
                )
            )
            self.assertIsInstance(action, ucapi.SetupComplete)
            self.assertEqual(store.settings().api_key, "existing-key")


if __name__ == "__main__":
    unittest.main()
