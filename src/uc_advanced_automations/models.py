"""Configuration models and validation helpers."""

from __future__ import annotations

import re
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

COMMAND_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
VALID_STEP_TYPES = {"command", "delay", "condition", "wait", "http", "log"}
VALID_OPERATORS = {
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "contains",
    "not_contains",
    "in",
    "not_in",
    "exists",
    "not_exists",
    "truthy",
    "falsy",
    "between",
    "outside",
}


class Settings(BaseModel):
    """Runtime settings."""

    core_url: str = "ws://remote.local/ws"
    api_key: str = ""
    web_host: str = "0.0.0.0"
    web_port: int = Field(default=8099, ge=1, le=65535)
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


class StateTrigger(BaseModel):
    """Run an automation when one entity attribute changes."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    type: Literal["entity_state"] = "entity_state"
    enabled: bool = True
    entity_id: str = Field(min_length=1, max_length=160)
    attribute: str = Field(default="state", min_length=1, max_length=160)
    from_value: Any = None
    to_value: Any = None
    debounce_ms: int = Field(default=0, ge=0, le=86_400_000)
    cooldown_ms: int = Field(default=0, ge=0, le=86_400_000)

    @field_validator("entity_id", "attribute")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value is required")
        return value


class Automation(BaseModel):
    """One advanced automation with command and/or background triggers."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(min_length=1, max_length=80)
    command: str = Field(min_length=2, max_length=64)
    command_enabled: bool = True
    description: str = Field(default="", max_length=240)
    enabled: bool = True
    mode: Literal["single", "parallel"] = "single"
    triggers: list[StateTrigger] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: str) -> str:
        value = value.strip().upper().replace(" ", "_").replace("-", "_")
        if not COMMAND_RE.fullmatch(value):
            raise ValueError("command must contain only A-Z, 0-9 and underscores")
        return value

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for index, step in enumerate(value):
            validate_step(step, f"steps[{index}]")
        return value

    @model_validator(mode="after")
    def unique_trigger_ids(self) -> "Automation":
        trigger_ids = [trigger.id for trigger in self.triggers]
        if len(trigger_ids) != len(set(trigger_ids)):
            raise ValueError("trigger ids must be unique within an automation")
        return self


class AppConfig(BaseModel):
    """Persisted application configuration."""

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


def validate_step(step: dict[str, Any], path: str = "step") -> None:
    """Validate an automation step recursively."""

    if not isinstance(step, dict):
        raise ValueError(f"{path} must be an object")
    step_type = step.get("type")
    if step_type not in VALID_STEP_TYPES:
        raise ValueError(f"{path}.type must be one of: {', '.join(sorted(VALID_STEP_TYPES))}")

    if step_type == "command":
        _required_string(step, "entity_id", path)
        _required_string(step, "cmd_id", path)
        params = step.get("params", {})
        if not isinstance(params, dict):
            raise ValueError(f"{path}.params must be an object")

    elif step_type == "delay":
        milliseconds = step.get("milliseconds")
        if not isinstance(milliseconds, (int, float)) or not 0 <= milliseconds <= 86_400_000:
            raise ValueError(f"{path}.milliseconds must be between 0 and 86400000")

    elif step_type == "condition":
        validate_condition_group(step, path)
        for branch in ("then", "else"):
            steps = step.get(branch, [])
            if not isinstance(steps, list):
                raise ValueError(f"{path}.{branch} must be an array")
            for index, child in enumerate(steps):
                validate_step(child, f"{path}.{branch}[{index}]")

    elif step_type == "wait":
        validate_condition_group(step, path)
        timeout_ms = step.get("timeout_ms", 30_000)
        interval_ms = step.get("interval_ms", 500)
        if not isinstance(timeout_ms, (int, float)) or not 1 <= timeout_ms <= 86_400_000:
            raise ValueError(f"{path}.timeout_ms must be between 1 and 86400000")
        if not isinstance(interval_ms, (int, float)) or not 100 <= interval_ms <= 60_000:
            raise ValueError(f"{path}.interval_ms must be between 100 and 60000")

    elif step_type == "http":
        method = str(step.get("method", "POST")).upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError(f"{path}.method is not supported")
        url = _required_string(step, "url", path)
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"{path}.url must start with http:// or https://")
        headers = step.get("headers", {})
        if not isinstance(headers, dict):
            raise ValueError(f"{path}.headers must be an object")

    elif step_type == "log":
        _required_string(step, "message", path)


def validate_condition_group(step: dict[str, Any], path: str) -> None:
    mode = step.get("mode", "all")
    if mode not in {"all", "any"}:
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
        _required_string(condition, "start", path)
        _required_string(condition, "end", path)
        weekdays = condition.get("weekdays", list(range(7)))
        if not isinstance(weekdays, list) or any(day not in range(7) for day in weekdays):
            raise ValueError(f"{path}.weekdays must contain values from 0 to 6")
    else:
        raise ValueError(f"{path}.kind must be entity or time")


def _required_string(data: dict[str, Any], key: str, path: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}.{key} is required")
    return value.strip()
