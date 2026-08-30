from __future__ import annotations

import asyncio
import inspect
import unittest
from collections import defaultdict
from typing import Any

from uc_advanced_automations.step_model_extensions import install_model_extensions

install_model_extensions()

from uc_advanced_automations.extended_engine import ExtendedAutomationEngine
from uc_advanced_automations.models import Automation


class FakeCore:
    def __init__(self) -> None:
        self.commands: list[tuple[str, str, dict[str, Any] | None]] = []
        self.entities: dict[str, dict[str, Any]] = {
            "device.tv": {"entity_id": "device.tv", "attributes": {"state": "ON"}},
            "macro.movie": {"entity_id": "macro.movie", "attributes": {"state": "ON"}},
            "activity.movie": {"entity_id": "activity.movie", "attributes": {"state": "OFF"}},
        }
        self.listeners: dict[str, list[Any]] = defaultdict(list)

    async def connect(self, force: bool = False) -> None:
        return None

    async def get_entity(self, entity_id: str) -> dict[str, Any]:
        return self.entities[entity_id]

    async def execute_entity_command(
        self,
        entity_id: str,
        command_id: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.commands.append((entity_id, command_id, params))
        if command_id == "activity.on":
            self.entities[entity_id]["attributes"]["state"] = "ON"
        elif command_id == "activity.off":
            self.entities[entity_id]["attributes"]["state"] = "OFF"
        return {}

    def add_event_listener(self, message: str, callback: Any) -> None:
        if callback not in self.listeners[message]:
            self.listeners[message].append(callback)

    def remove_event_listener(self, message: str, callback: Any) -> None:
        if callback in self.listeners[message]:
            self.listeners[message].remove(callback)

    async def emit(self, message: str, data: dict[str, Any]) -> None:
        for callback in tuple(self.listeners[message]):
            result = callback(data)
            if inspect.isawaitable(result):
                await result


async def wait_for_run(
    engine: ExtendedAutomationEngine,
    automation_id: str,
    run_id: str,
    timeout: float = 2.0,
) -> dict[str, Any]:
    async with asyncio.timeout(timeout):
        while True:
            summary = engine.database.run_summary(automation_id, recent_limit=100)
            record = next(
                (item for item in summary["recent_runs"] if item["run_id"] == run_id),
                None,
            )
            if record is not None and record["status"] != "running":
                return record
            await asyncio.sleep(0.01)


class SequenceStepTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.core = FakeCore()
        self.automations: dict[str, Automation] = {}
        self.engine = ExtendedAutomationEngine(
            self.core,
            lambda: "Europe/Berlin",
            automation_resolver=self.automations.get,
        )

    async def asyncTearDown(self) -> None:
        await self.engine.close()

    async def test_values_choose_command_sequence_macro_activity_and_stop(self) -> None:
        automation = Automation(
            name="Sequence features",
            command="SEQUENCE_FEATURES",
            entity_ids=["device.tv", "macro.movie", "activity.movie"],
            steps=[
                {"type": "set_variable", "name": "mode", "source": "literal", "value": "movie"},
                {
                    "type": "template",
                    "name": "mode_upper",
                    "template": "{{ mode|upper }}",
                    "output_type": "string",
                },
                {
                    "type": "choose",
                    "expression": "{{ mode_upper }}",
                    "cases": [
                        {
                            "name": "Movie",
                            "operator": "eq",
                            "value": "MOVIE",
                            "steps": [
                                {
                                    "type": "command_sequence",
                                    "mode": "commands",
                                    "commands": [
                                        {
                                            "entity_id": "device.tv",
                                            "cmd_id": "remote.send",
                                            "params": {"command": "{{ mode|upper }}"},
                                            "delay_ms": 0,
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                    "default_steps": [{"type": "log", "message": "default should not run"}],
                },
                {"type": "activity", "activity_id": "activity.movie", "action": "on"},
                {"type": "command_sequence", "mode": "macro", "macro_id": "macro.movie"},
                {"type": "stop_automation", "target": "current"},
                {"type": "command", "entity_id": "device.tv", "cmd_id": "should.not.run"},
            ],
        )
        self.automations[automation.id] = automation

        result = self.engine.start(automation, "test")
        self.assertTrue(result.accepted)
        record = await wait_for_run(self.engine, automation.id, result.run_id)

        self.assertEqual("stopped", record["status"])
        self.assertEqual(
            [
                ("device.tv", "remote.send", {"command": "MOVIE"}),
                ("activity.movie", "activity.on", None),
                ("macro.movie", "macro.run", None),
            ],
            self.core.commands,
        )

    async def test_wait_for_event_filters_and_stores_payload(self) -> None:
        automation = Automation(
            name="Wait event",
            command="WAIT_EVENT",
            steps=[
                {
                    "type": "wait_event",
                    "event": "entity_change",
                    "filters": {"entity_id": "sensor.one"},
                    "timeout_ms": 1000,
                    "store_as": "event",
                },
                {"type": "log", "message": "received {{ event.entity_id }}={{ event.value }}"},
            ],
        )
        self.automations[automation.id] = automation
        result = self.engine.start(automation, "test")

        async with asyncio.timeout(1):
            while not self.core.listeners["entity_change"]:
                await asyncio.sleep(0.01)
        await self.core.emit("entity_change", {"entity_id": "sensor.other", "value": 1})
        await self.core.emit("entity_change", {"entity_id": "sensor.one", "value": 42})

        record = await wait_for_run(self.engine, automation.id, result.run_id)
        self.assertEqual("success", record["status"])
        messages = [
            item["message"]
            for item in self.engine.database.logs_after()
            if item["run_id"] == result.run_id
        ]
        self.assertIn("received sensor.one=42", messages)
        self.assertFalse(self.core.listeners["entity_change"])

    async def test_run_automation_waits_and_can_pass_variables(self) -> None:
        child = Automation(
            name="Child",
            command="CHILD",
            entity_ids=["device.tv"],
            steps=[
                {
                    "type": "command",
                    "entity_id": "device.tv",
                    "cmd_id": "set.mode",
                    "params": {"mode": "{{ requested_mode }}"},
                }
            ],
        )
        parent = Automation(
            name="Parent",
            command="PARENT",
            steps=[
                {"type": "set_variable", "name": "requested_mode", "source": "literal", "value": "cinema"},
                {
                    "type": "run_automation",
                    "automation_id": child.id,
                    "wait": True,
                    "propagate_failure": True,
                    "pass_variables": True,
                },
            ],
        )
        self.automations[child.id] = child
        self.automations[parent.id] = parent

        result = self.engine.start(parent, "test")
        record = await wait_for_run(self.engine, parent.id, result.run_id)

        self.assertEqual("success", record["status"])
        self.assertIn(("device.tv", "set.mode", {"mode": "cinema"}), self.core.commands)
        child_summary = self.engine.database.run_summary(child.id)
        self.assertEqual("success", child_summary["last_run"]["status"])

    def test_new_entity_references_are_part_of_relationship_validation(self) -> None:
        with self.assertRaises(ValueError):
            Automation(
                name="Missing selections",
                command="MISSING_SELECTIONS",
                entity_ids=[],
                steps=[{"type": "activity", "activity_id": "activity.movie", "action": "on"}],
            )


if __name__ == "__main__":
    unittest.main()
