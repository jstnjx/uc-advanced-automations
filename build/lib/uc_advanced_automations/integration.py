"""Unfolded Circle Integration API entity exposed to the Remote."""

from __future__ import annotations

import logging
from typing import Any

import ucapi
from ucapi import remote
from ucapi.ui import Size, UiPage, create_ui_text

from .config_store import ConfigStore
from .engine import AutomationEngine

_LOG = logging.getLogger(__name__)
ENTITY_ID = "advanced_automations"


class IntegrationController:
    """Maintain the single virtual Remote entity and dispatch commands."""

    def __init__(self, api: ucapi.IntegrationAPI, store: ConfigStore, engine: AutomationEngine) -> None:
        self.api = api
        self.store = store
        self.engine = engine

    def sync_entity(self) -> None:
        entity = self._build_entity()
        self.api.available_entities.remove(ENTITY_ID)
        self.api.available_entities.add(entity)

        if self.api.configured_entities.contains(ENTITY_ID):
            self.api.configured_entities.remove(ENTITY_ID)
            self.api.configured_entities.add(entity)

    def _build_entity(self) -> ucapi.Remote:
        automations = [item for item in self.store.automations() if item.enabled]
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
        _websocket: Any,
    ) -> ucapi.StatusCodes:
        if cmd_id != remote.Commands.SEND_CMD:
            return ucapi.StatusCodes.NOT_IMPLEMENTED
        command = str((params or {}).get("command", "")).upper()
        automation = self.store.get_by_command(command)
        if not automation or not automation.enabled:
            return ucapi.StatusCodes.NOT_FOUND

        result = self.engine.start(automation, source="Unfolded Circle Remote")
        return ucapi.StatusCodes.OK if result.accepted else ucapi.StatusCodes.CONFLICT
