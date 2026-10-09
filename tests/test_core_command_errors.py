# Advanced Automations v2.0.0
"""Ensure command failures identify the target without exposing command parameters."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from uc_advanced_automations.core_client import CoreApiError, CoreClient


class CoreCommandDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_command_reports_entity_and_command_without_params(self) -> None:
        client = CoreClient(lambda: None)
        backend = SimpleNamespace(
            put_entity_command=AsyncMock(side_effect=RuntimeError("HTTP 500"))
        )
        with patch.object(
            client, "_ensure_api", new=AsyncMock(return_value=backend)
        ):
            with self.assertRaises(CoreApiError) as error:
                await client.execute_entity_command(
                    "media_player.living_room",
                    "play",
                    {"api_key": "must-not-appear"},
                )

        self.assertEqual(error.exception.code, 500)
        self.assertIn("media_player.living_room", str(error.exception))
        self.assertIn("play", str(error.exception))
        self.assertNotIn("must-not-appear", str(error.exception))
        backend.put_entity_command.assert_awaited_once_with(
            "media_player.living_room", "play", {"api_key": "must-not-appear"}
        )


if __name__ == "__main__":
    unittest.main()
