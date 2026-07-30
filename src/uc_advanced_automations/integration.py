"""Unfolded Circle Integration API entity exposed to the Remote."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import ucapi
from ucapi import remote
from ucapi.ui import Size, UiPage, create_ui_text

from .config_store import ConfigStore
from .core_client import CoreApiError, CoreClient
from .engine import AutomationEngine

_LOG = logging.getLogger(__name__)
ENTITY_ID = "advanced_automations"


class IntegrationController:
    """Maintain the virtual Remote entity, dispatch commands and refresh its Core definition."""

    def __init__(
        self,
        api: ucapi.IntegrationAPI,
        store: ConfigStore,
        engine: AutomationEngine,
        core: CoreClient,
    ) -> None:
        self.api = api
        self.store = store
        self.engine = engine
        self.core = core
        self._definition_signature = ""
        self._refresh_lock = asyncio.Lock()
        self._last_refresh: dict[str, Any] = {"status": "not-run", "changed": False}

    @property
    def last_refresh(self) -> dict[str, Any]:
        return dict(self._last_refresh)

    def sync_entity(self) -> bool:
        """Replace the local Integration-API entity and report definition changes."""
        entity = self._build_entity()
        signature = json.dumps(entity.options or {}, sort_keys=True, separators=(",", ":"))
        changed = signature != self._definition_signature
        self._definition_signature = signature

        self.api.available_entities.remove(ENTITY_ID)
        self.api.available_entities.add(entity)

        if self.api.configured_entities.contains(ENTITY_ID):
            self.api.configured_entities.remove(ENTITY_ID)
            self.api.configured_entities.add(entity)
        return changed

    async def sync_and_refresh(self, force: bool = False) -> dict[str, Any]:
        changed = self.sync_entity()
        if not changed and not force:
            result = {"status": "unchanged", "changed": False, "refreshed": False}
            self._last_refresh = result
            return result
        result = await self.refresh_remote_entity(force=force)
        result["changed"] = changed
        self._last_refresh = result
        return result

    async def refresh_remote_entity(self, force: bool = False) -> dict[str, Any]:
        """Refresh generated commands/pages without requiring a manual integration reload.

        Core first reloads the integration's available entities. If the configured entity
        still contains stale options, the Core integration connection is cycled and the
        entity is checked again. Existing entity/profile references are preserved.
        """
        async with self._refresh_lock:
            if not self.store.settings().api_key:
                return {
                    "status": "api-key-required",
                    "refreshed": False,
                    "message": "Run integration setup to create the Remote Core API key.",
                }
            try:
                configured = await self._find_core_entity()
                if configured is None:
                    return {
                        "status": "not-configured",
                        "refreshed": False,
                        "message": "The Advanced Automations entity has not been added to the Remote yet.",
                    }
                integration_id = configured.get("integration_id")
                if not isinstance(integration_id, str) or not integration_id:
                    return {"status": "integration-not-found", "refreshed": False}

                desired = self._desired_options()
                if not force and self._options_match(configured.get("options"), desired):
                    return {"status": "current", "refreshed": True, "reloaded": False}

                await self.core.refresh_available_entities(integration_id)
                updated = await self._poll_options(desired, timeout=2.0)
                if updated:
                    return {"status": "refreshed", "refreshed": True, "reloaded": False}

                # Core can cache configured entity options until the integration reconnects.
                await self.core.integration_command(integration_id, "DISCONNECT")
                await asyncio.sleep(0.35)
                await self.core.integration_command(integration_id, "CONNECT")
                await self.core.refresh_available_entities(integration_id)
                updated = await self._poll_options(desired, timeout=5.0)
                return {
                    "status": "reloaded" if updated else "refresh-pending",
                    "refreshed": updated,
                    "reloaded": True,
                    "message": None if updated else "Core accepted the reload; the UI may update after the integration reconnects.",
                }
            except CoreApiError as err:
                _LOG.warning("Automatic entity refresh failed: %s", err)
                return {"status": "failed", "refreshed": False, "message": str(err)}

    async def _find_core_entity(self) -> dict[str, Any] | None:
        entities = await self.core.get_entities()
        candidates = []
        for entity in entities:
            entity_id = str(entity.get("entity_id", ""))
            integration_id = str(entity.get("integration_id", ""))
            if entity_id == ENTITY_ID or entity_id.endswith(f".{ENTITY_ID}"):
                candidates.append(entity)
            elif "advanced_automations" in integration_id or "advanced-automations" in integration_id:
                candidates.append(entity)
        if not candidates:
            return None
        candidate = candidates[0]
        entity_id = candidate.get("entity_id")
        if isinstance(entity_id, str) and entity_id:
            try:
                return await self.core.get_entity(entity_id)
            except CoreApiError:
                pass
        return candidate

    async def _poll_options(self, desired: dict[str, Any], timeout: float) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            entity = await self._find_core_entity()
            if entity and self._options_match(entity.get("options"), desired):
                return True
            await asyncio.sleep(0.25)
        return False

    def _desired_options(self) -> dict[str, Any]:
        return dict(self._build_entity().options or {})

    @staticmethod
    def _options_match(current: Any, desired: dict[str, Any]) -> bool:
        if not isinstance(current, dict):
            return False
        return current.get("simple_commands") == desired.get("simple_commands") and current.get(
            "user_interface"
        ) == desired.get("user_interface")

    def _build_entity(self) -> ucapi.Remote:
        automations = [
            item for item in self.store.automations() if item.enabled and item.command_enabled
        ]
        commands = [item.command for item in automations]
        pages = self._build_pages(automations)
        return ucapi.Remote(
            ENTITY_ID,
            {"en": "Advanced Automations", "de": "Erweiterte Automationen"},
            [remote.Features.SEND_CMD],
            {remote.Attributes.STATE: remote.States.ON},
            simple_commands=commands or None,
            ui_pages=pages,
            description={
                "en": "Conditional sequences configured in the local web interface.",
                "de": "Bedingte Sequenzen aus der lokalen Weboberfläche.",
            },
            cmd_handler=self.command_handler,
        )

    def _build_pages(self, automations) -> list[UiPage]:
        if not automations:
            page = UiPage("empty", "Automations")
            page.add(create_ui_text("Open the web interface to create an automation", 0, 1, size=Size(4, 2)))
            return [page]

        pages: list[UiPage] = []
        for page_index, start in enumerate(range(0, len(automations), 12), start=1):
            page = UiPage(f"automations_{page_index}", "Automations" if page_index == 1 else f"Automations {page_index}")
            for local_index, automation in enumerate(automations[start : start + 12]):
                column = local_index % 2
                row = local_index // 2
                label = automation.name[:28]
                page.add(
                    create_ui_text(
                        label,
                        column * 2,
                        row,
                        size=Size(2, 1),
                        cmd=remote.create_send_cmd(automation.command),
                    )
                )
            pages.append(page)
        return pages

    async def command_handler(
        self,
        _entity: ucapi.Remote,
        cmd_id: str,
        params: dict[str, Any] | None,
        *,
        websocket: Any,
    ) -> ucapi.StatusCodes:
        if cmd_id != remote.Commands.SEND_CMD:
            return ucapi.StatusCodes.NOT_IMPLEMENTED
        command = str((params or {}).get("command", "")).upper()
        automation = self.store.get_by_command(command)
        if not automation or not automation.enabled or not automation.command_enabled:
            return ucapi.StatusCodes.NOT_FOUND

        result = self.engine.start(automation, source="Unfolded Circle Remote")
        return ucapi.StatusCodes.OK if result.accepted else ucapi.StatusCodes.CONFLICT
