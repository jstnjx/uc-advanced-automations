"""Configuration models, trigger schemas, and validation helpers."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Union
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

COMMAND_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
VALID_STEP_TYPES = {"command", "delay", "condition", "wait", "parallel", "http", "log"}
VALID_OPERATORS = {
    "eq", "ne", "gt", "gte", "lt", "lte", "contains", "not_contains",
    "in", "not_in", "exists", "not_exists", "truthy", "falsy", "between", "outside",
}


def new_id() -> str:
    return str(uuid4())


class Settings(BaseModel):
    """Runtime settings."""

    core_url: str = "ws://remote.local/ws"
    api_key: str = ""
    web_host: str = "0.0.0.0"
    web_port: int = Field(default=9201, ge=9201, le=65535)
    timezone: str = "Europe/Berlin"
    request_timeout_seconds: float = Field(default=10, ge=1, le=120)

    @field_validator("core_url")
    @classmethod
    def validate_core_url(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(("ws://", "wss://")):
            raise ValueError("core_url must start with ws:// or wss://")
        if not value.endswith("/ws"):
            value = value.rstrip("/") + "/ws"
        return value


class TriggerBase(BaseModel):
    id: str = Field(default_factory=new_id)
    enabled: bool = True
    cooldown_ms: int = Field(default=0, ge=0, le=86_400_000)


class StateTrigger(TriggerBase):
    """Run when one entity attribute transitions."""

    type: Literal["entity_state"] = "entity_state"
    entity_id: str = Field(min_length=1, max_length=160)
    attribute: str = Field(default="state", min_length=1, max_length=160)
    from_value: Any = None
    to_value: Any = None
    debounce_ms: int = Field(default=0, ge=0, le=86_400_000)

    @field_validator("entity_id", "attribute")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value is required")
        return value


class EntityDurationTrigger(TriggerBase):
    """Run when an entity remains at a value for a duration."""

    type: Literal["entity_duration"] = "entity_duration"
    entity_id: str = Field(min_length=1, max_length=160)
    attribute: str = Field(default="state", min_length=1, max_length=160)
    value: Any = None
    duration_ms: int = Field(default=10_000, ge=100, le=86_400_000)


class NumericThresholdTrigger(TriggerBase):
    """Run when a numeric value crosses a threshold."""

    type: Literal["numeric_threshold"] = "numeric_threshold"
    entity_id: str = Field(min_length=1, max_length=160)
    attribute: str = Field(default="value", min_length=1, max_length=160)
    threshold: float = 0
    direction: Literal["above", "below", "crosses"] = "above"
    hysteresis: float = Field(default=0, ge=0)


class AnyAttributeTrigger(TriggerBase):
    """Run when any attribute, or one optional attribute, changes."""

    type: Literal["entity_change"] = "entity_change"
    entity_id: str = Field(min_length=1, max_length=160)
    attribute: str = Field(default="", max_length=160)


class ScheduledTimeTrigger(TriggerBase):
    """Run at a local scheduled time."""

    type: Literal["schedule"] = "schedule"
    time: str = "08:00"
    weekdays: list[int] = Field(default_factory=lambda: list(range(7)))

    @field_validator("time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        if not TIME_RE.fullmatch(value.strip()):
            raise ValueError("time must use HH:MM")
        return value.strip()

    @field_validator("weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[int]) -> list[int]:
        if not value or any(day not in range(7) for day in value):
            raise ValueError("weekdays must contain values from 0 to 6")
        return sorted(set(value))


class PeriodicTrigger(TriggerBase):
    """Run repeatedly at a fixed interval."""

    type: Literal["interval"] = "interval"
    interval_ms: int = Field(default=60_000, ge=1_000, le=31_536_000_000)
    start_delay_ms: int = Field(default=0, ge=0, le=31_536_000_000)


class RemoteEventTrigger(TriggerBase):
    """Run on the initial Remote connection or a later reconnect."""

    type: Literal["remote_event"] = "remote_event"
    event: Literal["startup", "reconnect"] = "reconnect"


class WebhookTrigger(TriggerBase):
    """Run through the local webhook endpoint."""

    type: Literal["webhook"] = "webhook"
    webhook_id: str = Field(default_factory=new_id, min_length=8, max_length=160)


class AutomationOutcomeTrigger(TriggerBase):
    """Run after another automation completes or fails."""

    type: Literal["automation_outcome"] = "automation_outcome"
    automation_id: str = Field(min_length=1, max_length=160)
    outcome: Literal["success", "failure", "any"] = "success"


class ManualTrigger(TriggerBase):
    """Run from a virtual button in the web interface."""

    type: Literal["manual"] = "manual"
    label: str = Field(default="Run automation", min_length=1, max_length=80)


Trigger = Annotated[
    Union[
        StateTrigger,
        EntityDurationTrigger,
        NumericThresholdTrigger,
        AnyAttributeTrigger,
        ScheduledTimeTrigger,
        PeriodicTrigger,
        RemoteEventTrigger,
        WebhookTrigger,
        AutomationOutcomeTrigger,
        ManualTrigger,
    ],
    Field(discriminator="type"),
]


class Automation(BaseModel):
    """One automation with triggers, execution policies, and recovery flows."""

    id: str = Field(default_factory=new_id)
    name: str = Field(min_length=1, max_length=80)
    command: str = Field(min_length=2, max_length=64)
    command_enabled: bool = True
    description: str = Field(default="", max_length=240)
    enabled: bool = True
    mode: Literal["single", "replace", "parallel"] = "single"
    max_runtime_ms: int = Field(default=0, ge=0, le=604_800_000)
    entity_ids: list[str] = Field(default_factory=list)
    trigger_mode: Literal["any", "all"] = "any"
    triggers: list[Trigger] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    cancellation_steps: list[dict[str, Any]] = Field(default_factory=list)
    rollback_steps: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        migrated.setdefault("max_runtime_ms", 0)
        migrated.setdefault("cancellation_steps", [])
        migrated.setdefault("rollback_steps", [])
        migrated["steps"] = migrate_steps(migrated.get("steps", []))
        migrated["cancellation_steps"] = migrate_steps(migrated.get("cancellation_steps", []))
        migrated["rollback_steps"] = migrate_steps(migrated.get("rollback_steps", []))
        for trigger in migrated.get("triggers", []) or []:
            if isinstance(trigger, dict):
                trigger.setdefault("type", "entity_state")
                trigger.setdefault("cooldown_ms", 0)
        if "entity_ids" not in migrated or migrated.get("entity_ids") is None:
            migrated["entity_ids"] = collect_entity_ids(
                migrated.get("triggers", []),
                migrated.get("steps", []),
                migrated.get("cancellation_steps", []),
                migrated.get("rollback_steps", []),
            )
        return migrated

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: str) -> str:
        value = value.strip().upper().replace(" ", "_").replace("-", "_")
        if not COMMAND_RE.fullmatch(value):
            raise ValueError("command must contain only A-Z, 0-9 and underscores")
        return value

    @field_validator("entity_ids")
    @classmethod
    def validate_entity_ids(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in value:
            entity_id = str(item).strip()
            if not entity_id or entity_id in seen:
                continue
            if len(entity_id) > 160:
                raise ValueError("entity identifiers must contain at most 160 characters")
            seen.add(entity_id)
            result.append(entity_id)
        return result

    @field_validator("steps", "cancellation_steps", "rollback_steps")
    @classmethod
    def validate_steps_field(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for index, step in enumerate(value):
            validate_step(step, f"steps[{index}]")
        return value

    @model_validator(mode="after")
    def validate_relationships(self) -> "Automation":
        trigger_ids = [trigger.id for trigger in self.triggers]
        if len(trigger_ids) != len(set(trigger_ids)):
            raise ValueError("trigger ids must be unique within an automation")
        referenced = set(
            collect_entity_ids(
                self.triggers, self.steps, self.cancellation_steps, self.rollback_steps
            )
        )
        missing = sorted(referenced - set(self.entity_ids))
        if missing:
            raise ValueError("select every referenced entity in the Entities step: " + ", ".join(missing))
        return self


class AppConfig(BaseModel):
    """Persisted application configuration."""

    schema_version: int = 1
    settings: Settings = Field(default_factory=Settings)
    automations: list[Automation] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_automation_fields(self) -> "AppConfig":
        ids = [item.id for item in self.automations]
        commands = [item.command for item in self.automations if item.command_enabled]
        if len(ids) != len(set(ids)):
            raise ValueError("automation ids must be unique")
        if len(commands) != len(set(commands)):
            raise ValueError("enabled automation commands must be unique")
        return self


def migrate_steps(items: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            result.append(raw)
            continue
        step = dict(raw)
        if step.get("continue_on_error") and "failure_action" not in step:
            step["failure_action"] = "continue"
        step.setdefault("execution_timeout_ms", 0)
        step.setdefault("retry_count", 0)
        step.setdefault("retry_delay_ms", 1000)
        step.setdefault("retry_backoff", "fixed")
        step.setdefault("failure_action", "fail")
        step.setdefault("failure_steps", [])
        step["failure_steps"] = migrate_steps(step.get("failure_steps", []))
        if step.get("type") == "condition":
            step["then"] = migrate_steps(step.get("then", []))
            step["else"] = migrate_steps(step.get("else", []))
        elif step.get("type") == "wait":
            legacy = step.get("wait_type", "condition")
            if "time_reference" not in step:
                step["time_reference"] = "trigger" if legacy == "trigger_timeframe" else "step"
            if "on_match" not in step:
                step["on_match"] = "stop" if legacy == "trigger_timeframe" else "continue"
            if "on_timeout" not in step:
                step["on_timeout"] = "continue" if legacy == "trigger_timeframe" else "fail"
            step.setdefault("match_steps", [])
            step.setdefault("timeout_steps", [])
            step["match_steps"] = migrate_steps(step.get("match_steps", []))
            step["timeout_steps"] = migrate_steps(step.get("timeout_steps", []))
            step.pop("wait_type", None)
        elif step.get("type") == "parallel":
            branches = []
            for index, branch in enumerate(step.get("branches", []) or []):
                if isinstance(branch, dict):
                    branches.append({
                        "name": str(branch.get("name") or f"Branch {index + 1}"),
                        "steps": migrate_steps(branch.get("steps", [])),
                    })
                elif isinstance(branch, list):
                    branches.append({"name": f"Branch {index + 1}", "steps": migrate_steps(branch)})
            step["branches"] = branches
            step.setdefault("wait_for", "all")
        result.append(step)
    return result


def collect_entity_ids(triggers: Any, *step_groups: Any) -> list[str]:
    """Collect referenced entity identifiers while preserving first-use order."""
    result: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        if isinstance(value, str) and value.strip() and value not in seen:
            seen.add(value)
            result.append(value)

    for trigger in triggers or []:
        if isinstance(trigger, (StateTrigger, EntityDurationTrigger, NumericThresholdTrigger, AnyAttributeTrigger)):
            add(trigger.entity_id)
        elif isinstance(trigger, dict) and trigger.get("type", "entity_state") in {
            "entity_state", "entity_duration", "numeric_threshold", "entity_change"
        }:
            add(trigger.get("entity_id"))

    def walk(items: Any) -> None:
        for step in items or []:
            if not isinstance(step, dict):
                continue
            if step.get("type") == "command":
                add(step.get("entity_id"))
            if step.get("type") in {"condition", "wait"}:
                for condition in step.get("conditions", []) or []:
                    if isinstance(condition, dict) and condition.get("kind", "entity") == "entity":
                        add(condition.get("entity_id"))
            for key in ("then", "else", "failure_steps", "match_steps", "timeout_steps"):
                walk(step.get(key, []))
            if step.get("type") == "parallel":
                for branch in step.get("branches", []) or []:
                    if isinstance(branch, dict):
                        walk(branch.get("steps", []))

    for group in step_groups:
        walk(group)
    return result


def validate_step(step: dict[str, Any], path: str = "step") -> None:
    if not isinstance(step, dict):
        raise ValueError(f"{path} must be an object")
    step_type = step.get("type")
    if step_type not in VALID_STEP_TYPES:
        raise ValueError(f"{path}.type must be one of: {', '.join(sorted(VALID_STEP_TYPES))}")
    validate_execution_policy(step, path)

    if step_type == "command":
        _required_string(step, "entity_id", path)
        _required_string(step, "cmd_id", path)
        if not isinstance(step.get("params", {}), dict):
            raise ValueError(f"{path}.params must be an object")
    elif step_type == "delay":
        _number_range(step.get("milliseconds"), 0, 86_400_000, f"{path}.milliseconds")
    elif step_type == "condition":
        validate_condition_group(step, path)
        _validate_step_lists(step, path, "then", "else")
    elif step_type == "wait":
        validate_condition_group(step, path)
        _number_range(step.get("timeout_ms", 30_000), 1, 86_400_000, f"{path}.timeout_ms")
        _number_range(step.get("interval_ms", 500), 100, 60_000, f"{path}.interval_ms")
        if step.get("time_reference", "step") not in {"trigger", "step"}:
            raise ValueError(f"{path}.time_reference must be trigger or step")
        if step.get("on_match", "continue") not in {"continue", "stop", "branch"}:
            raise ValueError(f"{path}.on_match is not supported")
        if step.get("on_timeout", "fail") not in {"continue", "stop", "fail", "branch"}:
            raise ValueError(f"{path}.on_timeout is not supported")
        _validate_step_lists(step, path, "match_steps", "timeout_steps")
    elif step_type == "parallel":
        branches = step.get("branches", [])
        if not isinstance(branches, list) or len(branches) < 2:
            raise ValueError(f"{path}.branches must contain at least two branches")
        if step.get("wait_for", "all") not in {"all", "any"}:
            raise ValueError(f"{path}.wait_for must be all or any")
        for index, branch in enumerate(branches):
            if not isinstance(branch, dict):
                raise ValueError(f"{path}.branches[{index}] must be an object")
            _required_string(branch, "name", f"{path}.branches[{index}]")
            children = branch.get("steps", [])
            if not isinstance(children, list):
                raise ValueError(f"{path}.branches[{index}].steps must be an array")
            for child_index, child in enumerate(children):
                validate_step(child, f"{path}.branches[{index}].steps[{child_index}]")
    elif step_type == "http":
        method = str(step.get("method", "POST")).upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError(f"{path}.method is not supported")
        url = _required_string(step, "url", path)
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"{path}.url must start with http:// or https://")
        if not isinstance(step.get("headers", {}), dict):
            raise ValueError(f"{path}.headers must be an object")
    elif step_type == "log":
        _required_string(step, "message", path)


def validate_execution_policy(step: dict[str, Any], path: str) -> None:
    _number_range(step.get("execution_timeout_ms", 0), 0, 86_400_000, f"{path}.execution_timeout_ms")
    _number_range(step.get("retry_count", 0), 0, 20, f"{path}.retry_count")
    _number_range(step.get("retry_delay_ms", 1000), 0, 86_400_000, f"{path}.retry_delay_ms")
    if step.get("retry_backoff", "fixed") not in {"fixed", "exponential"}:
        raise ValueError(f"{path}.retry_backoff must be fixed or exponential")
    if step.get("failure_action", "fail") not in {"fail", "continue", "branch", "rollback"}:
        raise ValueError(f"{path}.failure_action is not supported")
    failure_steps = step.get("failure_steps", [])
    if not isinstance(failure_steps, list):
        raise ValueError(f"{path}.failure_steps must be an array")
    for index, child in enumerate(failure_steps):
        validate_step(child, f"{path}.failure_steps[{index}]")


def _validate_step_lists(step: dict[str, Any], path: str, *keys: str) -> None:
    for key in keys:
        children = step.get(key, [])
        if not isinstance(children, list):
            raise ValueError(f"{path}.{key} must be an array")
        for index, child in enumerate(children):
            validate_step(child, f"{path}.{key}[{index}]")


def validate_condition_group(step: dict[str, Any], path: str) -> None:
    if step.get("mode", "all") not in {"all", "any"}:
        raise ValueError(f"{path}.mode must be all or any")
    conditions = step.get("conditions", [])
    if not isinstance(conditions, list) or not conditions:
        raise ValueError(f"{path}.conditions must contain at least one condition")
    for index, condition in enumerate(conditions):
        validate_condition(condition, f"{path}.conditions[{index}]")


def validate_condition(condition: dict[str, Any], path: str = "condition") -> None:
    if not isinstance(condition, dict):
        raise ValueError(f"{path} must be an object")
    kind = condition.get("kind", "entity")
    operator = condition.get("operator", "eq")
    if operator not in VALID_OPERATORS:
        raise ValueError(f"{path}.operator is not supported")
    if kind == "entity":
        if operator in {"between", "outside"}:
            raise ValueError(f"{path}: between and outside are only valid for time conditions")
        _required_string(condition, "entity_id", path)
        _required_string(condition, "attribute", path)
    elif kind == "time":
        if operator not in {"between", "outside"}:
            raise ValueError(f"{path}: time conditions support between or outside")
        start = _required_string(condition, "start", path)
        end = _required_string(condition, "end", path)
        if not TIME_RE.fullmatch(start) or not TIME_RE.fullmatch(end):
            raise ValueError(f"{path}: time values must use HH:MM")
        weekdays = condition.get("weekdays", list(range(7)))
        if not isinstance(weekdays, list) or any(day not in range(7) for day in weekdays):
            raise ValueError(f"{path}.weekdays must contain values from 0 to 6")
    else:
        raise ValueError(f"{path}.kind must be entity or time")


def _number_range(value: Any, minimum: float, maximum: float, path: str) -> None:
    if not isinstance(value, (int, float)) or not minimum <= value <= maximum:
        raise ValueError(f"{path} must be between {minimum:g} and {maximum:g}")


def _required_string(data: dict[str, Any], key: str, path: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}.{key} is required")
    return value.strip()
