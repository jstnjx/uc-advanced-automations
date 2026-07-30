from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from uc_advanced_automations.config_store import ConfigStore
from uc_advanced_automations.engine import AutomationEngine, compare_values
from uc_advanced_automations.models import Automation


class FakeCore:
    def __init__(self):
        self.entities = {
            "switch.test": {"entity_id": "switch.test", "attributes": {"state": "OFF", "level": 5}}
        }
        self.commands = []

    async def get_entity(self, entity_id):
        return self.entities[entity_id]

    async def execute_entity_command(self, entity_id, command_id, params=None):
        self.commands.append((entity_id, command_id, params))
        if command_id == "on":
            self.entities[entity_id]["attributes"]["state"] = "ON"
        return {}


class ComparisonTests(unittest.TestCase):
    def test_numeric_comparison(self):
        self.assertTrue(compare_values("10", "gt", 5))
        self.assertTrue(compare_values(5, "lte", "5"))

    def test_membership_and_existence(self):
        self.assertTrue(compare_values("ON", "in", ["ON", "IDLE"]))
        self.assertTrue(compare_values(["a", "b"], "contains", "b"))


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.core = FakeCore()
        self.engine = AutomationEngine(self.core, lambda: "Europe/Berlin")

    async def asyncTearDown(self):
        await self.engine.close()

    async def test_conditional_branch(self):
        automation = Automation(
            name="Test",
            command="TEST_RUN",
            steps=[
                {
                    "type": "condition",
                    "mode": "all",
                    "conditions": [
                        {
                            "kind": "entity",
                            "entity_id": "switch.test",
                            "attribute": "state",
                            "operator": "eq",
                            "value": "OFF",
                        }
                    ],
                    "then": [
                        {
                            "type": "command",
                            "entity_id": "switch.test",
                            "cmd_id": "on",
                            "params": {},
                        }
                    ],
                    "else": [],
                }
            ],
        )
        result = self.engine.start(automation, "test")
        self.assertTrue(result.accepted)
        while self.engine.running_count():
            await asyncio.sleep(0.01)
        self.assertEqual(self.core.commands, [("switch.test", "on", None)])

    async def test_single_mode_rejects_second_run(self):
        automation = Automation(
            name="Slow",
            command="SLOW_RUN",
            steps=[{"type": "delay", "milliseconds": 100}],
        )
        first = self.engine.start(automation, "test")
        second = self.engine.start(automation, "test")
        self.assertTrue(first.accepted)
        self.assertFalse(second.accepted)
        while self.engine.running_count():
            await asyncio.sleep(0.01)


class ConfigStoreTests(unittest.TestCase):
    def test_creates_private_config(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory))
            self.assertTrue(store.path.exists())
            self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)


class RuntimeDetectionTests(unittest.TestCase):
    def test_remote_mode_uses_remote_defaults(self):
        from unittest.mock import patch

        from uc_advanced_automations.runtime import detect_runtime

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ",
            {"UC_CONFIG_HOME": directory},
            clear=True,
        ):
            runtime = detect_runtime()
            self.assertEqual(runtime.mode, "remote")
            self.assertEqual(runtime.default_core_url, "ws://127.0.0.1/ws")
            self.assertEqual(runtime.data_dir, Path(directory).resolve())

    def test_external_mode_can_be_forced(self):
        from unittest.mock import patch

        from uc_advanced_automations.runtime import detect_runtime

        with patch.dict(
            "os.environ",
            {"UC_EXTERNAL": "true", "UC_CONFIG_HOME": "/ignored"},
            clear=True,
        ):
            runtime = detect_runtime()
            self.assertEqual(runtime.mode, "external")
            self.assertEqual(runtime.default_core_url, "ws://remote.local/ws")


class CoreClientProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_core_request_envelope_and_api_key(self):
        import json
        from types import SimpleNamespace

        import websockets

        from uc_advanced_automations.core_client import CoreClient

        seen_header = None

        async def handler(connection):
            nonlocal seen_header
            seen_header = connection.request.headers.get("API-KEY")
            async for raw in connection:
                request = json.loads(raw)
                if request["msg"] == "get_entities":
                    payload = {
                        "kind": "resp",
                        "req_id": request["id"],
                        "msg": "entities",
                        "code": 200,
                        "msg_data": {
                            "entities": [{"entity_id": "switch.test", "entity_type": "switch"}],
                            "paging": {"count": 1, "limit": 100, "page": 1},
                        },
                    }
                    await connection.send(json.dumps(payload))

        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        settings = SimpleNamespace(
            core_url=f"ws://127.0.0.1:{port}/ws",
            api_key="secret-key",
            request_timeout_seconds=2,
        )
        client = CoreClient(lambda: settings)
        try:
            entities = await client.get_entities()
            self.assertEqual(entities[0]["entity_id"], "switch.test")
            self.assertEqual(seen_header, "secret-key")
        finally:
            await client.close()
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
