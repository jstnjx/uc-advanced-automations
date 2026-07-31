"""Entity, schedule, webhook, lifecycle, and automation-outcome triggers."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from .config_store import ConfigStore
from .core_client import CoreApiError, CoreClient
from .engine import AutomationEngine, _MISSING, _get_path, compare_values
from .models import (
    AnyAttributeTrigger,
    Automation,
    AutomationOutcomeTrigger,
    EntityDurationTrigger,
    ManualTrigger,
    NumericThresholdTrigger,
    PeriodicTrigger,
    RemoteEventTrigger,
    ScheduledTimeTrigger,
    StateTrigger,
    Trigger,
    WebhookTrigger,
)

_LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class TriggerBinding:
    automation: Automation
    trigger: Trigger


class TriggerManager:
    """Maintain all trigger bindings and launch matching automations."""

    def __init__(self, core: CoreClient, store: ConfigStore, engine: AutomationEngine) -> None:
        self.core, self.store, self.engine = core, store, engine
        self._entity_bindings: dict[str, list[TriggerBinding]] = defaultdict(list)
        self._webhooks: dict[str, list[TriggerBinding]] = defaultdict(list)
        self._manual: dict[str, TriggerBinding] = {}
        self._outcomes: dict[str, list[TriggerBinding]] = defaultdict(list)
        self._scheduled: list[TriggerBinding] = []
        self._intervals: list[TriggerBinding] = []
        self._remote_events: list[TriggerBinding] = []
        self._state: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, asyncio.Task[None]] = {}
        self._last_fired: dict[str, float] = {}
        self._last_schedule_key: dict[str, str] = {}
        self._interval_next: dict[str, float] = {}
        self._supervisor: asyncio.Task[None] | None = None
        self._scheduler: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._closed = False
        self._last_error: str | None = None
        self.core.add_event_listener("entity_change", self._on_entity_change)
        self.core.add_event_listener("connection", self._on_connection)
        self.engine.add_completion_listener(self._on_automation_completed)
        self.reload()

    @property
    def trigger_count(self) -> int:
        return sum(
            [
                sum(len(items) for items in self._entity_bindings.values()),
                sum(len(items) for items in self._webhooks.values()),
                len(self._manual), len(self._scheduled), len(self._intervals),
                len(self._remote_events), sum(len(items) for items in self._outcomes.values()),
            ]
        )

    @property
    def tracked_entity_count(self) -> int:
        return len(self._entity_bindings)

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def start(self) -> None:
        self._closed = False
        if self._supervisor is None or self._supervisor.done():
            self._supervisor = asyncio.create_task(self._run_supervisor(), name="automation-trigger-supervisor")
        if self._scheduler is None or self._scheduler.done():
            self._scheduler = asyncio.create_task(self._run_scheduler(), name="automation-trigger-scheduler")

    def reload(self) -> None:
        entity: dict[str, list[TriggerBinding]] = defaultdict(list)
        webhooks: dict[str, list[TriggerBinding]] = defaultdict(list)
        manual: dict[str, TriggerBinding] = {}
        outcomes: dict[str, list[TriggerBinding]] = defaultdict(list)
        scheduled: list[TriggerBinding] = []
        intervals: list[TriggerBinding] = []
        remote_events: list[TriggerBinding] = []
        active_ids: set[str] = set()
        for automation in self.store.automations():
            if not automation.enabled:
                continue
            for trigger in automation.triggers:
                if not trigger.enabled:
                    continue
                binding = TriggerBinding(automation, trigger)
                active_ids.add(trigger.id)
                if isinstance(trigger, (StateTrigger, EntityDurationTrigger, NumericThresholdTrigger, AnyAttributeTrigger)):
                    entity[trigger.entity_id].append(binding)
                elif isinstance(trigger, WebhookTrigger):
                    webhooks[trigger.webhook_id].append(binding)
                elif isinstance(trigger, ManualTrigger):
                    manual[trigger.id] = binding
                elif isinstance(trigger, AutomationOutcomeTrigger):
                    outcomes[trigger.automation_id].append(binding)
                elif isinstance(trigger, ScheduledTimeTrigger):
                    scheduled.append(binding)
                elif isinstance(trigger, PeriodicTrigger):
                    intervals.append(binding)
                elif isinstance(trigger, RemoteEventTrigger):
                    remote_events.append(binding)
        self._entity_bindings, self._webhooks, self._manual = entity, webhooks, manual
        self._outcomes, self._scheduled, self._intervals, self._remote_events = outcomes, scheduled, intervals, remote_events
        self._state = {key: value for key, value in self._state.items() if key in entity}
        self._last_fired = {key: value for key, value in self._last_fired.items() if key in active_ids}
        self._last_schedule_key = {key: value for key, value in self._last_schedule_key.items() if key in active_ids}
        self._interval_next = {key: value for key, value in self._interval_next.items() if key in active_ids}
        for trigger_id, task in list(self._pending.items()):
            if trigger_id not in active_ids:
                task.cancel()
                self._pending.pop(trigger_id, None)
        self._wake.set()

    async def close(self) -> None:
        self._closed = True
        self.core.remove_event_listener("entity_change", self._on_entity_change)
        self.core.remove_event_listener("connection", self._on_connection)
        self._wake.set()
        tasks = [task for task in (self._supervisor, self._scheduler) if task]
        tasks.extend(self._pending.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()
        self._supervisor = self._scheduler = None

    async def fire_webhook(self, webhook_id: str, payload: Any = None) -> list[str]:
        run_ids: list[str] = []
        for binding in self._webhooks.get(webhook_id, []):
            run_id = self._fire(binding, f"Webhook {webhook_id}")
            if run_id:
                run_ids.append(run_id)
        return run_ids

    def fire_manual(self, trigger_id: str) -> str | None:
        binding = self._manual.get(trigger_id)
        return self._fire(binding, f"Manual virtual button: {binding.trigger.label}") if binding else None

    async def _run_supervisor(self) -> None:
        while not self._closed:
            self._wake.clear()
            if not self._entity_bindings and not self._remote_events:
                await self._wait_or_timeout(60)
                continue
            if not self.store.settings().api_key:
                self._last_error = "Remote API key is not configured"
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
            except Exception as err:  # pragma: no cover
                self._last_error = str(err)
                _LOG.exception("Background trigger supervisor failed")
                await self._wait_or_timeout(5)

    async def _run_scheduler(self) -> None:
        while not self._closed:
            now_mono = monotonic()
            try:
                zone = ZoneInfo(self.store.settings().timezone)
            except Exception:
                zone = ZoneInfo("UTC")
            now = datetime.now(zone)
            schedule_key = now.strftime("%Y-%m-%d %H:%M")
            current_time = now.strftime("%H:%M")
            for binding in list(self._scheduled):
                trigger = binding.trigger
                if isinstance(trigger, ScheduledTimeTrigger) and now.weekday() in trigger.weekdays and current_time == trigger.time:
                    if self._last_schedule_key.get(trigger.id) != schedule_key:
                        self._last_schedule_key[trigger.id] = schedule_key
                        self._fire(binding, f"Scheduled time {trigger.time}")
            for binding in list(self._intervals):
                trigger = binding.trigger
                if not isinstance(trigger, PeriodicTrigger):
                    continue
                next_fire = self._interval_next.setdefault(
                    trigger.id, now_mono + trigger.start_delay_ms / 1000
                )
                if now_mono >= next_fire:
                    self._interval_next[trigger.id] = now_mono + trigger.interval_ms / 1000
                    self._fire(binding, f"Periodic interval {trigger.interval_ms} ms")
            await asyncio.sleep(1)

    async def _on_connection(self, data: dict[str, Any]) -> None:
        if data.get("event") != "connected":
            return
        first_connection = bool(data.get("first_connection"))
        expected = "startup" if first_connection else "reconnect"
        source = "Initial Remote connection" if first_connection else "Remote reconnected"
        for binding in list(self._remote_events):
            trigger = binding.trigger
            if isinstance(trigger, RemoteEventTrigger) and trigger.event == expected:
                self._fire(binding, source)

    async def _on_automation_completed(
        self, automation: Automation, _run_id: str, status: str, _error: str | None
    ) -> None:
        outcome = "failure" if status == "failure" else "success"
        for binding in list(self._outcomes.get(automation.id, [])):
            trigger = binding.trigger
            if isinstance(trigger, AutomationOutcomeTrigger) and trigger.outcome in {"any", outcome}:
                self._fire(binding, f"Automation {automation.name} completed with {status}")

    async def _wait_or_timeout(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _prime_states(self) -> None:
        for entity_id in list(self._entity_bindings):
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
            self._state.clear()
            self._wake.set()
            return
        if entity_id not in self._entity_bindings:
            return
        if str(data.get("event_type", "UPDATE")).upper() in {"DELETE", "REMOVED"}:
            self._state.pop(entity_id, None)
            self._cancel_entity_pending(entity_id)
            return
        attributes = self._extract_attributes(data)
        if not attributes:
            return
        previous = self._state.setdefault(entity_id, {})
        before = dict(previous)
        previous.update(attributes)
        for binding in list(self._entity_bindings.get(entity_id, [])):
            trigger = binding.trigger
            if isinstance(trigger, AnyAttributeTrigger):
                if trigger.attribute:
                    old, new = _get_path(before, trigger.attribute), _get_path(previous, trigger.attribute)
                    if _get_path(attributes, trigger.attribute) is _MISSING or old is _MISSING or compare_values(old, "eq", new):
                        continue
                    self._schedule(binding, old, new, 0)
                elif before != previous:
                    self._schedule(binding, before, dict(previous), 0)
                continue
            attribute = getattr(trigger, "attribute", "state")
            if _get_path(attributes, attribute) is _MISSING:
                continue
            old, new = _get_path(before, attribute), _get_path(previous, attribute)
            if new is _MISSING:
                continue
            if isinstance(trigger, StateTrigger):
                if old is _MISSING or compare_values(old, "eq", new):
                    continue
                if trigger.from_value is not None and not compare_values(old, "eq", trigger.from_value):
                    continue
                if trigger.to_value is not None and not compare_values(new, "eq", trigger.to_value):
                    continue
                self._schedule(binding, old, new, trigger.debounce_ms)
            elif isinstance(trigger, EntityDurationTrigger):
                pending = self._pending.pop(trigger.id, None)
                if pending:
                    pending.cancel()
                if trigger.value is None or compare_values(new, "eq", trigger.value):
                    self._schedule(binding, old, new, trigger.duration_ms, require_current=True)
            elif isinstance(trigger, NumericThresholdTrigger) and old is not _MISSING:
                if self._threshold_crossed(old, new, trigger):
                    self._schedule(binding, old, new, 0)

    @staticmethod
    def _extract_attributes(data: dict[str, Any]) -> dict[str, Any] | None:
        new_state = data.get("new_state")
        if isinstance(new_state, dict):
            candidate = new_state.get("attributes", new_state)
            if isinstance(candidate, dict):
                return candidate
        return data.get("attributes") if isinstance(data.get("attributes"), dict) else None

    @staticmethod
    def _threshold_crossed(old: Any, new: Any, trigger: NumericThresholdTrigger) -> bool:
        try:
            old_value, new_value = float(old), float(new)
        except (TypeError, ValueError):
            return False
        threshold, hysteresis = trigger.threshold, trigger.hysteresis
        above = old_value <= threshold - hysteresis and new_value > threshold
        below = old_value >= threshold + hysteresis and new_value < threshold
        return above if trigger.direction == "above" else below if trigger.direction == "below" else above or below

    def _schedule(
        self,
        binding: TriggerBinding,
        old_value: Any,
        new_value: Any,
        delay_ms: int,
        *,
        require_current: bool = False,
    ) -> None:
        if self._cooldown_active(binding.trigger):
            return
        previous = self._pending.pop(binding.trigger.id, None)
        if previous:
            previous.cancel()
        task = asyncio.create_task(
            self._fire_after_delay(binding, old_value, new_value, delay_ms, require_current),
            name=f"trigger-{binding.trigger.id}",
        )
        self._pending[binding.trigger.id] = task
        task.add_done_callback(lambda completed, trigger_id=binding.trigger.id: self._remove_pending(trigger_id, completed))

    async def _fire_after_delay(
        self,
        binding: TriggerBinding,
        old_value: Any,
        new_value: Any,
        delay_ms: int,
        require_current: bool,
    ) -> None:
        if delay_ms:
            await asyncio.sleep(delay_ms / 1000)
        trigger = binding.trigger
        if require_current and hasattr(trigger, "entity_id") and hasattr(trigger, "attribute"):
            current = _get_path(self._state.get(trigger.entity_id, {}), trigger.attribute)
            expected = getattr(trigger, "value", new_value)
            if current is _MISSING or (expected is not None and not compare_values(current, "eq", expected)):
                return
        self._fire(binding, self._source_for_entity_trigger(trigger, old_value, new_value))

    @staticmethod
    def _source_for_entity_trigger(trigger: Trigger, old_value: Any, new_value: Any) -> str:
        if isinstance(trigger, EntityDurationTrigger):
            return f"Entity remained in state: {trigger.entity_id}.{trigger.attribute}={new_value!r}"
        if isinstance(trigger, NumericThresholdTrigger):
            return f"Numeric threshold: {trigger.entity_id}.{trigger.attribute} {old_value!r} → {new_value!r}"
        if isinstance(trigger, AnyAttributeTrigger):
            return f"Entity changed: {trigger.entity_id}{'.' + trigger.attribute if trigger.attribute else ''}"
        return f"State trigger: {trigger.entity_id}.{trigger.attribute} {old_value!r} → {new_value!r}"

    def _fire(self, binding: TriggerBinding | None, source: str) -> str | None:
        if binding is None or self._cooldown_active(binding.trigger):
            return None
        if binding.automation.trigger_mode == "all" and not self._all_state_predicates_match(binding.automation):
            return None
        self._last_fired[binding.trigger.id] = monotonic()
        result = self.engine.start(binding.automation, source=source)
        if not result.accepted:
            _LOG.info("Trigger run rejected for %s: %s", binding.automation.name, result.reason)
        return result.run_id if result.accepted else None

    def _cooldown_active(self, trigger: Trigger) -> bool:
        last = self._last_fired.get(trigger.id)
        return last is not None and monotonic() - last < trigger.cooldown_ms / 1000

    def _all_state_predicates_match(self, automation: Automation) -> bool:
        predicates = [
            trigger for trigger in automation.triggers
            if trigger.enabled and isinstance(trigger, (StateTrigger, EntityDurationTrigger, NumericThresholdTrigger))
        ]
        for trigger in predicates:
            current = _get_path(self._state.get(trigger.entity_id, {}), trigger.attribute)
            if current is _MISSING:
                return False
            if isinstance(trigger, StateTrigger) and trigger.to_value is not None and not compare_values(current, "eq", trigger.to_value):
                return False
            if isinstance(trigger, EntityDurationTrigger) and trigger.value is not None and not compare_values(current, "eq", trigger.value):
                return False
            if isinstance(trigger, NumericThresholdTrigger):
                try:
                    value = float(current)
                except (TypeError, ValueError):
                    return False
                if trigger.direction == "above" and value <= trigger.threshold:
                    return False
                if trigger.direction == "below" and value >= trigger.threshold:
                    return False
        return True

    def _cancel_entity_pending(self, entity_id: str) -> None:
        for binding in self._entity_bindings.get(entity_id, []):
            task = self._pending.pop(binding.trigger.id, None)
            if task:
                task.cancel()

    def _remove_pending(self, trigger_id: str, task: asyncio.Task[None]) -> None:
        if self._pending.get(trigger_id) is task:
            self._pending.pop(trigger_id, None)
        if not task.cancelled():
            error = task.exception()
            if error:
                _LOG.error("Trigger task failed: %s", error)
