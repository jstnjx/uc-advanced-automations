from __future__ import annotations

import json
import unittest

from pydantic import ValidationError

from uc_advanced_automations.models import Automation
from uc_advanced_automations.web import (
    AutomationValidationError,
    _validate_command_targets,
    error_middleware,
)


class ValidationResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_pydantic_value_error_context_is_json_safe(self):
        async def handler(_request):
            Automation.model_validate(
                {
                    "name": "Broken",
                    "command": "BROKEN",
                    "steps": [{"type": "command", "entity_id": "switch.test", "cmd_id": ""}],
                }
            )

        response = await error_middleware(object(), handler)
        self.assertEqual(response.status, 400)
        payload = json.loads(response.text)
        self.assertEqual(payload["error"], "Validation failed")
        self.assertIsInstance(payload["details"], list)
        self.assertTrue(payload["details"])
        json.dumps(payload)
        self.assertIn("cmd_id", payload["details"][0]["msg"])

    async def test_sensor_command_target_is_rejected_with_structured_details(self):
        class Core:
            async def get_entities(self):
                return [
                    {"entity_id": "sensor.temperature", "entity_type": "sensor"},
                    {"entity_id": "switch.test", "entity_type": "switch"},
                ]

        class Request:
            app = {"core": Core()}

        automation = Automation.model_validate(
            {
                "name": "Bad target",
                "command": "BAD_TARGET",
                "steps": [
                    {
                        "type": "command",
                        "entity_id": "sensor.temperature",
                        "cmd_id": "on",
                        "params": {},
                    }
                ],
            }
        )
        with self.assertRaises(AutomationValidationError) as context:
            await _validate_command_targets(Request(), automation)
        self.assertEqual(context.exception.details[0]["type"], "sensor_is_read_only")
        self.assertIn("cannot receive commands", context.exception.details[0]["msg"])


if __name__ == "__main__":
    unittest.main()
