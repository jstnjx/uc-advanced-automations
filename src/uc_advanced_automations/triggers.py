"""Background automation triggers driven by Remote Core entity-change events."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from time import monotonic
from typing import Any

from .config_store import ConfigStore
from .core_client import CoreApiError, CoreClient
from .engine import AutomationEngine, _MISSING, _get_path, compare_values
from .models import Automation, StateTrigger

_LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class TriggerBinding:
    automation: Automation
    trigger: StateTrigger


class TriggerManager:
    """Subscribe to entity events and launch matching automations."""

    def __init__(self, core: CoreClient, store: ConfigStore, engine: AutomationEngine) -> None:
        self.core = core
        self.store = store
        self.engine = engine
        self._bindings: dict[str, list[TriggerBinding]] = defaultdict(list)
        self._state: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, asyncio.Task[None]] = {}
        self._last_fired: dict[str, float] = {}
        self._supervisor: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._closed = False
        self._last_error: str | None = None
        self.core.add_event_listener("entity_change", self._on_entity_change)
        self.reload()

    @property
    def trigger_count(self) -> int:
        return sum(len(items) for items in self._bindings.values())

    @property
    def tracked_entity_count(self) -> int:
        return len(self._bindings)

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def start(self) -> None:
        if self._supervisor is None or self._supervisor.done():
            self._closed = False
            self._supervisor = asyncio.create_task(self._run_supervisor(), name="automation-trigger-supervisor")

    def reload(self) -> None:
        """Rebuild trigger bindings after configuration changes."""
        bindings: dict[str, list[TriggerBinding]] = defaultdict(list)
        active_ids: set[str] = set()
        for automation in self.store.automations():
            if not automation.enabled:
                continue
            for trigger in automation.triggers:
                if trigger.enabled:
                    bindings[trigger.entity_id].append(TriggerBinding(automation, trigger))
                    active_ids.add(trigger.id)

        self._bindings = bindings
        self._state = {entity_id: state for entity_id, state in self._state.items() if entity_id in bindings}
        self._last_fired = {trigger_id: value for trigger_id, value in self._last_fired.items() if trigger_id in active_ids}
        for trigger_id, task in list(self._pending.items()):
            if trigger_id not in active_ids:
                task.cancel()
                self._pending.pop(trigger_id, None)
        self._wake.set()

    async def close(self) -> None:
        self._closed = True
        self.core.remove_event_listener("entity_change", self._on_entity_change)
        self._wake.set()
        if self._supervisor:
            self._supervisor.cancel()
            await asyncio.gather(self._supervisor, return_exceptions=True)
            self._supervisor = None
        tasks = list(self._pending.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()

    async def _run_supervisor(self) -> None:
        while not self._closed:
            self._wake.clear()
            if not self._bindings:
                await self._wait_or_timeout(60)
                continue
            if not self.store.settings().api_key:
                self._last_error = "Remote Core API key is not configured"
                await self._wait_or_timeout(10)
                continue
            try:
                await self.core.connect()
                await self._prime_states()
                self._last_error = None
                await self._wait_or_timeout(30)
            except asyncio.CancelledError:
                raise
            except CoreApiError as err:
                self._last_error = str(err)
                _LOG.warning("Background trigger connection failed: %s", err)
                await self._wait_or_timeout(5)
            except Exception as err:  # pragma: no cover - safety boundary
                self._last_error = str(err)
                _LOG.exception("Background trigger supervisor failed")
                await self._wait_or_timeout(5)

    async def _wait_or_timeout(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _prime_states(self) -> None:
        for entity_id in list(self._bindings):
            try:
                entity = await self.core.get_entity(entity_id)
            except CoreApiError as err:
                _LOG.warning("Unable to prime trigger entity %s: %s", entity_id, err)
                continue
            attributes = entity.get("attributes")
            if isinstance(attributes, dict):
                self._state[entity_id] = dict(attributes)

    async def _on_entity_change(self, data: dict[str, Any]) -> None:
        entity_id = data.get("entity_id")
        if not isinstance(entity_id, str):
            # Bulk/resync events don't identify one entity. Re-prime all tracked entities.
            self._state.clear()
            self._wake.set()
            return
        if entity_id not in self._bindings:
            return

        event_type = str(data.get("event_type", "UPDATE")).upper()
        if event_type in {"DELETE", "REMOVED"}:
            self._state.pop(entity_id, None)
            return

        new_state = data.get("new_state")
        attributes: dict[str, Any] | None = None
        if isinstance(new_state, dict):
            candidate = new_state.get("attributes", new_state)
            if isinstance(candidate, dict):
                attributes = candidate
        if attributes is None and isinstance(data.get("attributes"), dict):
            attributes = data["attributes"]
        if not attributes:
            return

        previous = self._state.setdefault(entity_id, {})
        before = dict(previous)
        previous.update(attributes)

        for binding in list(self._bindings.get(entity_id, [])):
            trigger = binding.trigger
            # Core update events are partial. Only evaluate when the watched attribute was part of this update.
            changed_value = _get_path(attributes, trigger.attribute)
            if changed_value is _MISSING:
                continue
            old_value = _get_path(before, trigger.attribute)
            new_value = _get_path(previous, trigger.attribute)
            if old_value is _MISSING or new_value is _MISSING or compare_values(old_value, "eq", new_value):
                continue
            if trigger.from_value is not None and not compare_values(old_value, "eq", trigger.from_value):
                continue
            if trigger.to_value is not None and not compare_values(new_value, "eq", trigger.to_value):
                continue
            self._schedule(binding, old_value, new_value)

    def _schedule(self, binding: TriggerBinding, old_value: Any, new_value: Any) -> None:
        trigger = binding.trigger
        now = monotonic()
        last = self._last_fired.get(trigger.id)
        if last is not None and now - last < trigger.cooldown_ms / 1000:
            return

        previous = self._pending.pop(trigger.id, None)
        if previous:
            previous.cancel()
        task = asyncio.create_task(
            self._fire_after_debounce(binding, old_value, new_value),
            name=f"state-trigger-{trigger.id}",
        )
        self._pending[trigger.id] = task
        task.add_done_callback(lambda completed, trigger_id=trigger.id: self._remove_pending(trigger_id, completed))

    async def _fire_after_debounce(self, binding: TriggerBinding, old_value: Any, new_value: Any) -> None:
        trigger = binding.trigger
        if trigger.debounce_ms:
            await asyncio.sleep(trigger.debounce_ms / 1000)
            current = _get_path(self._state.get(trigger.entity_id, {}), trigger.attribute)
            if current is _MISSING or not compare_values(current, "eq", new_value):
                return

        now = monotonic()
        last = self._last_fired.get(trigger.id)
        if last is not None and now - last < trigger.cooldown_ms / 1000:
            return
        self._last_fired[trigger.id] = now
        source = f"state trigger: {trigger.entity_id}.{trigger.attribute} {old_value!r} → {new_value!r}"
        result = self.engine.start(binding.automation, source=source)
        if not result.accepted:
            _LOG.info("Trigger run rejected for %s: %s", binding.automation.name, result.reason)

    def _remove_pending(self, trigger_id: str, task: asyncio.Task[None]) -> None:
        if self._pending.get(trigger_id) is task:
            self._pending.pop(trigger_id, None)
        if not task.cancelled():
            error = task.exception()
            if error:
                _LOG.error("State trigger task failed: %s", error)
