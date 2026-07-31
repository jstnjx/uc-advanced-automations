"""Integration API entities exposed to the Remote."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import ucapi
from ucapi import remote, sensor
from ucapi.ui import Size, UiPage, create_ui_text

from .config_store import ConfigStore
from .core_client import CoreApiError, CoreClient
from .engine import AutomationEngine
from .models import Automation

_LOG = logging.getLogger(__name__)
ENTITY_ID = "advanced_automations"
LAST_TRIGGERED_SENSOR_ID = "last_automation_triggered"


class IntegrationController:
    """Maintain integration entities, dispatch commands, and refresh definitions."""

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
        self._last_triggered_name = "No automation triggered yet"
        self.engine.add_start_listener(self._automation_started)

    @property
    def last_refresh(self) -> dict[str, Any]:
        return dict(self._last_refresh)

    def sync_entity(self) -> bool:
        """Replace local Integration API entities and report definition changes."""
        remote_entity = self._build_remote_entity()
        last_triggered_sensor = self._build_last_triggered_sensor()
        signature = json.dumps(remote_entity.options or {}, sort_keys=True, separators=(",", ":"))
        changed = signature != self._definition_signature
        self._definition_signature = signature

        self._replace_entity(remote_entity)
        self._replace_entity(last_triggered_sensor)
        return changed

    def _replace_entity(self, entity: Any) -> None:
        self.api.available_entities.remove(entity.id)
        self.api.available_entities.add(entity)
        if self.api.configured_entities.contains(entity.id):
            self.api.configured_entities.remove(entity.id)
            self.api.configured_entities.add(entity)

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
        """Refresh generated commands and pages without a manual integration reload."""
        async with self._refresh_lock:
            if not self.store.settings().api_key:
                return {
                    "status": "api-key-required",
                    "refreshed": False,
                    "message": "Run integration setup to create the Remote API key.",
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

                await self.core.integration_command(integration_id, "DISCONNECT")
                await asyncio.sleep(0.35)
                await self.core.integration_command(integration_id, "CONNECT")
                await self.core.refresh_available_entities(integration_id)
                updated = await self._poll_options(desired, timeout=5.0)
                return {
                    "status": "reloaded" if updated else "refresh-pending",
                    "refreshed": updated,
                    "reloaded": True,
                    "message": None
                    if updated
                    else "The reload was accepted; the entity definition may update after reconnection.",
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
                if str(entity.get("entity_type", "")).lower() == "remote":
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
        return dict(self._build_remote_entity().options or {})

    @staticmethod
    def _options_match(current: Any, desired: dict[str, Any]) -> bool:
        if not isinstance(current, dict):
            return False
        return current.get("simple_commands") == desired.get("simple_commands") and current.get(
            "user_interface"
        ) == desired.get("user_interface")

    def _build_remote_entity(self) -> ucapi.Remote:
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

    def _build_last_triggered_sensor(self) -> ucapi.Sensor:
        return ucapi.Sensor(
            LAST_TRIGGERED_SENSOR_ID,
            {"en": "Last automation triggered", "de": "Zuletzt ausgelöste Automation"},
            [],
            {
                sensor.Attributes.STATE: sensor.States.ON,
                sensor.Attributes.VALUE: self._last_triggered_name,
            },
            device_class=sensor.DeviceClasses.CUSTOM,
            description={
                "en": "Displays the most recently started automation.",
                "de": "Zeigt die zuletzt gestartete Automation an.",
            },
        )

    def _automation_started(self, automation: Automation, _run_id: str, _source: str) -> None:
        self._last_triggered_name = automation.name
        attributes = {
            sensor.Attributes.STATE: sensor.States.ON,
            sensor.Attributes.VALUE: automation.name,
        }
        self.api.available_entities.update_attributes(LAST_TRIGGERED_SENSOR_ID, attributes)
        if self.api.configured_entities.contains(LAST_TRIGGERED_SENSOR_ID):
            self.api.configured_entities.update_attributes(LAST_TRIGGERED_SENSOR_ID, attributes)

    def _build_pages(self, automations: list[Automation]) -> list[UiPage]:
        if not automations:
            page = UiPage("empty", "Automations")
            page.add(create_ui_text("Open the web interface to create an automation", 0, 1, size=Size(4, 2)))
            return [page]

        pages: list[UiPage] = []
        for page_index, start in enumerate(range(0, len(automations), 12), start=1):
            page = UiPage(
                f"automations_{page_index}",
                "Automations" if page_index == 1 else f"Automations {page_index}",
            )
            for local_index, automation in enumerate(automations[start : start + 12]):
                column = local_index % 2
                row = local_index // 2
                page.add(
                    create_ui_text(
                        automation.name[:28],
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

        result = self.engine.start(automation, source="Remote")
        return ucapi.StatusCodes.OK if result.accepted else ucapi.StatusCodes.CONFLICT
