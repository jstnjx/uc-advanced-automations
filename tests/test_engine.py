from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from uc_advanced_automations.config_store import ConfigStore
from uc_advanced_automations.core_client import CoreClient
from uc_advanced_automations.engine import AutomationEngine, compare_values
from uc_advanced_automations.models import Automation
from uc_advanced_automations.triggers import TriggerManager


class FakeCore:
    def __init__(self):
        self.entities = {
            "switch.test": {
                "entity_id": "switch.test",
                "entity_type": "switch",
                "attributes": {"state": "OFF", "level": 5},
                "options": {},
            },
            "switch.second": {
                "entity_id": "switch.second",
                "entity_type": "switch",
                "attributes": {"state": "OFF"},
                "options": {},
            },
            "sensor.temperature": {
                "entity_id": "sensor.temperature",
                "entity_type": "sensor",
                "attributes": {"state": 21.5, "unit": "°C"},
                "options": {},
            },
        }
        self.commands = []
        self.listeners = {}

    async def get_entity(self, entity_id):
        return self.entities[entity_id]

    async def execute_entity_command(self, entity_id, command_id, params=None):
        self.commands.append((entity_id, command_id, params))
        if command_id in {"on", "switch.on"}:
            self.entities[entity_id]["attributes"]["state"] = "ON"
        return {}

    def add_event_listener(self, message, callback):
        self.listeners.setdefault(message, []).append(callback)

    def remove_event_listener(self, message, callback):
        if callback in self.listeners.get(message, []):
            self.listeners[message].remove(callback)

    async def emit(self, message, data):
        for callback in self.listeners.get(message, []):
            result = callback(data)
            if asyncio.iscoroutine(result):
                await result

    async def connect(self):
        return None


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
                            "cmd_id": "switch.on",
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
        self.assertEqual(self.core.commands, [("switch.test", "switch.on", None)])

    async def test_single_mode_rejects_second_run(self):
        automation = Automation(name="Slow", command="SLOW_RUN", steps=[{"type": "delay", "milliseconds": 100}])
        first = self.engine.start(automation, "test")
        second = self.engine.start(automation, "test")
        self.assertTrue(first.accepted)
        self.assertFalse(second.accepted)
        while self.engine.running_count():
            await asyncio.sleep(0.01)

    async def test_replace_mode_cancels_active_run_and_starts_new_run(self):
        automation = Automation(
            name="Replace",
            command="REPLACE_RUN",
            mode="replace",
            steps=[{"type": "delay", "milliseconds": 80}, {"type": "log", "message": "finished"}],
        )
        first = self.engine.start(automation, "first")
        await asyncio.sleep(0.01)
        second = self.engine.start(automation, "second")
        self.assertTrue(first.accepted)
        self.assertTrue(second.accepted)
        while self.engine.running_count():
            await asyncio.sleep(0.01)
        messages = [item["message"] for item in self.engine.logs_after(0)]
        self.assertIn("Replacing active run", messages)
        self.assertTrue(any(message == "Cancelled" for message in messages))
        self.assertTrue(any(message == "finished" for message in messages))


class TriggerTests(unittest.IsolatedAsyncioTestCase):
    async def test_state_transition_runs_background_automation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory))
            automation = Automation(
                name="On transition",
                command="ON_TRANSITION",
                command_enabled=False,
                triggers=[
                    {
                        "entity_id": "switch.test",
                        "attribute": "state",
                        "from_value": "OFF",
                        "to_value": "ON",
                    }
                ],
                steps=[{"type": "log", "message": "triggered"}],
            )
            store.replace_automations([automation])
            core = FakeCore()
            engine = AutomationEngine(core, lambda: "Europe/Berlin")
            manager = TriggerManager(core, store, engine)
            manager._state["switch.test"] = {"state": "OFF"}
            try:
                await core.emit(
                    "entity_change",
                    {
                        "event_type": "UPDATE",
                        "entity_id": "switch.test",
                        "new_state": {"attributes": {"state": "ON"}},
                    },
                )
                for _ in range(50):
                    if any("triggered" in item["message"] for item in engine.logs_after(0)):
                        break
                    await asyncio.sleep(0.01)
                logs = engine.logs_after(0)
                self.assertTrue(any("state trigger" in item["message"] for item in logs))
                self.assertTrue(any("triggered" in item["message"] for item in logs))
            finally:
                await manager.close()
                await engine.close()

    async def test_and_trigger_mode_requires_all_target_states(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory))
            automation = Automation(
                name="Both on",
                command="BOTH_ON",
                command_enabled=False,
                trigger_mode="all",
                triggers=[
                    {"entity_id": "switch.test", "to_value": "ON"},
                    {"entity_id": "switch.second", "to_value": "ON"},
                ],
                steps=[{"type": "log", "message": "both matched"}],
            )
            store.replace_automations([automation])
            core = FakeCore()
            engine = AutomationEngine(core, lambda: "Europe/Berlin")
            manager = TriggerManager(core, store, engine)
            manager._state["switch.test"] = {"state": "OFF"}
            manager._state["switch.second"] = {"state": "OFF"}
            try:
                await core.emit("entity_change", {"entity_id": "switch.test", "new_state": {"attributes": {"state": "ON"}}})
                await asyncio.sleep(0.03)
                self.assertFalse(any("both matched" in item["message"] for item in engine.logs_after(0)))
                await core.emit("entity_change", {"entity_id": "switch.second", "new_state": {"attributes": {"state": "ON"}}})
                for _ in range(50):
                    if any("both matched" in item["message"] for item in engine.logs_after(0)):
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(any("both matched" in item["message"] for item in engine.logs_after(0)))
            finally:
                await manager.close()
                await engine.close()

    async def test_trigger_ignores_nonmatching_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory))
            automation = Automation(
                name="Only on",
                command="ONLY_ON",
                triggers=[{"entity_id": "switch.test", "from_value": "OFF", "to_value": "ON"}],
                steps=[{"type": "log", "message": "should not run"}],
            )
            store.replace_automations([automation])
            core = FakeCore()
            engine = AutomationEngine(core, lambda: "Europe/Berlin")
            manager = TriggerManager(core, store, engine)
            manager._state["switch.test"] = {"state": "OFF"}
            try:
                await core.emit(
                    "entity_change",
                    {"entity_id": "switch.test", "new_state": {"attributes": {"state": "IDLE"}}},
                )
                await asyncio.sleep(0.03)
                self.assertEqual(engine.running_count(), 0)
                self.assertEqual(engine.logs_after(0), [])
            finally:
                await manager.close()
                await engine.close()


class ConfigStoreTests(unittest.TestCase):
    def test_creates_private_config(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory))
            self.assertTrue(store.path.exists())
            self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)

    def test_old_command_only_configuration_remains_valid(self):
        automation = Automation.model_validate({"name": "Legacy", "command": "LEGACY", "steps": []})
        self.assertTrue(automation.command_enabled)
        self.assertEqual(automation.triggers, [])


class RuntimeDetectionTests(unittest.TestCase):
    def test_remote_mode_uses_remote_defaults(self):
        from uc_advanced_automations.runtime import detect_runtime

        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"UC_CONFIG_HOME": directory}, clear=True):
            runtime = detect_runtime()
            self.assertEqual(runtime.mode, "remote")
            self.assertEqual(runtime.default_core_url, "ws://127.0.0.1/ws")
            self.assertEqual(runtime.data_dir, Path(directory).resolve())

    def test_external_mode_can_be_forced(self):
        from uc_advanced_automations.runtime import detect_runtime

        with patch.dict("os.environ", {"UC_EXTERNAL": "true", "UC_CONFIG_HOME": "/ignored"}, clear=True):
            runtime = detect_runtime()
            self.assertEqual(runtime.mode, "external")
            self.assertEqual(runtime.default_core_url, "ws://remote.local/ws")


class CoreClientProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_core_subscription_metadata_and_api_key(self):
        import websockets

        seen_header = None
        seen_messages = []

        async def handler(connection):
            nonlocal seen_header
            seen_header = connection.request.headers.get("API-KEY")
            async for raw in connection:
                request = json.loads(raw)
                seen_messages.append(request["msg"])
                msg = request["msg"]
                if msg == "subscribe_events":
                    data = {}
                elif msg == "get_entities":
                    data = {
                        "entities": [
                            {
                                "entity_id": "switch.test",
                                "entity_type": "switch",
                                "attributes": {"state": "OFF"},
                                "options": {},
                            }
                        ],
                        "paging": {"count": 1, "limit": 100, "page": 1},
                    }
                elif msg == "get_entity":
                    data = {
                        "entity_id": "switch.test",
                        "entity_type": "switch",
                        "attributes": {"state": "OFF"},
                        "options": {},
                    }
                elif msg == "get_entity_commands":
                    data = {"commands": ["switch.on", "switch.off"]}
                elif msg == "get_entity_command_metadata":
                    data = [
                        {"id": "switch.on", "cmd_id": "on", "name": {"en": "On"}},
                        {"id": "switch.off", "cmd_id": "off", "name": {"en": "Off"}},
                    ]
                else:
                    data = {}
                await connection.send(
                    json.dumps(
                        {
                            "kind": "resp",
                            "req_id": request["id"],
                            "msg": msg,
                            "code": 200,
                            "msg_data": data,
                        }
                    )
                )

        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        settings = SimpleNamespace(
            core_url=f"ws://127.0.0.1:{port}/ws",
            api_key="secret-key",
            request_timeout_seconds=2,
        )
        client = CoreClient(lambda: settings)
        try:
            definitions = await client.get_command_definitions("switch.test")
            self.assertEqual(definitions["commands"][0]["id"], "switch.on")
            self.assertEqual(seen_header, "secret-key")
            self.assertIn("subscribe_events", seen_messages)
            self.assertIn("get_entity_command_metadata", seen_messages)
        finally:
            await client.close()
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
