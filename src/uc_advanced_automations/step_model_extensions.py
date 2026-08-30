# Advanced Automations v2.0.0
"""v2 sequence-step model extensions.

This module extends the existing dict-based sequence schema without changing the
persisted Automation model shape. Installation happens before ConfigStore loads
persisted automations so both old and new sequence steps are validated uniformly.
"""

from __future__ import annotations

import re
from typing import Any

from . import models

_NEW_STEP_TYPES = {
    "set_variable",
    "template",
    "choose",
    "wait_event",
    "run_automation",
    "stop_automation",
    "command_sequence",
    "activity",
}
_VARIABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_INSTALLED = False

_original_validate_step = models.validate_step
_original_migrate_steps = models.migrate_steps
_original_collect_entity_ids = models.collect_entity_ids


def _variable_name(value: Any, path: str, *, optional: bool = False) -> str:
    if value in (None, "") and optional:
        return ""
    if not isinstance(value, str) or not _VARIABLE_RE.fullmatch(value.strip()):
        raise ValueError(
            f"{path} must start with a letter or underscore and contain only "
            "letters, numbers and underscores"
        )
    return value.strip()


def _operator(value: Any, path: str) -> str:
    operator = str(value or "eq")
    if operator not in {
        "eq", "ne", "gt", "gte", "lt", "lte", "contains", "not_contains",
        "in", "not_in", "truthy", "falsy",
    }:
        raise ValueError(f"{path} is not supported")
    return operator


def _validate_extended_step(step: dict[str, Any], path: str) -> None:
    step_type = step.get("type")

    if step_type == "set_variable":
        _variable_name(step.get("name"), f"{path}.name")
        source = step.get("source", "literal")
        if source not in {"literal", "variable", "entity"}:
            raise ValueError(f"{path}.source must be literal, variable or entity")
        if source == "variable":
            _variable_name(step.get("source_variable"), f"{path}.source_variable")
        elif source == "entity":
            models._required_string(step, "entity_id", path)
            models._required_string(step, "attribute", path)

    elif step_type == "template":
        _variable_name(step.get("name"), f"{path}.name")
        models._required_string(step, "template", path)
        if step.get("output_type", "auto") not in {"auto", "string", "number", "boolean", "json"}:
            raise ValueError(f"{path}.output_type is not supported")
        if step.get("transform", "none") not in {"none", "lower", "upper", "trim", "length", "json"}:
            raise ValueError(f"{path}.transform is not supported")

    elif step_type == "choose":
        models._required_string(step, "expression", path)
        cases = step.get("cases", [])
        if not isinstance(cases, list) or not cases:
            raise ValueError(f"{path}.cases must contain at least one case")
        for index, case in enumerate(cases):
            case_path = f"{path}.cases[{index}]"
            if not isinstance(case, dict):
                raise ValueError(f"{case_path} must be an object")
            models._required_string(case, "name", case_path)
            _operator(case.get("operator", "eq"), f"{case_path}.operator")
            children = case.get("steps", [])
            if not isinstance(children, list):
                raise ValueError(f"{case_path}.steps must be an array")
            for child_index, child in enumerate(children):
                models.validate_step(child, f"{case_path}.steps[{child_index}]")
        default_steps = step.get("default_steps", [])
        if not isinstance(default_steps, list):
            raise ValueError(f"{path}.default_steps must be an array")
        for index, child in enumerate(default_steps):
            models.validate_step(child, f"{path}.default_steps[{index}]")

    elif step_type == "wait_event":
        models._required_string(step, "event", path)
        filters = step.get("filters", {})
        if not isinstance(filters, dict):
            raise ValueError(f"{path}.filters must be an object")
        for key in filters:
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"{path}.filters keys must be non-empty strings")
        models._number_range(step.get("timeout_ms", 30_000), 1, 86_400_000, f"{path}.timeout_ms")
        _variable_name(step.get("store_as", ""), f"{path}.store_as", optional=True)
        if step.get("on_timeout", "fail") not in {"fail", "continue", "stop"}:
            raise ValueError(f"{path}.on_timeout is not supported")

    elif step_type == "run_automation":
        models._required_string(step, "automation_id", path)
        for key in ("wait", "propagate_failure", "pass_variables"):
            if key in step and not isinstance(step[key], bool):
                raise ValueError(f"{path}.{key} must be a boolean")

    elif step_type == "stop_automation":
        target = step.get("target", "current")
        if target not in {"current", "automation"}:
            raise ValueError(f"{path}.target must be current or automation")
        if target == "automation":
            models._required_string(step, "automation_id", path)
        if "require_running" in step and not isinstance(step["require_running"], bool):
            raise ValueError(f"{path}.require_running must be a boolean")

    elif step_type == "command_sequence":
        mode = step.get("mode", "commands")
        if mode not in {"commands", "macro"}:
            raise ValueError(f"{path}.mode must be commands or macro")
        if mode == "macro":
            models._required_string(step, "macro_id", path)
        else:
            commands = step.get("commands", [])
            if not isinstance(commands, list) or not commands:
                raise ValueError(f"{path}.commands must contain at least one command")
            for index, command in enumerate(commands):
                command_path = f"{path}.commands[{index}]"
                if not isinstance(command, dict):
                    raise ValueError(f"{command_path} must be an object")
                models._required_string(command, "entity_id", command_path)
                models._required_string(command, "cmd_id", command_path)
                if not isinstance(command.get("params", {}), dict):
                    raise ValueError(f"{command_path}.params must be an object")
                models._number_range(command.get("delay_ms", 0), 0, 86_400_000, f"{command_path}.delay_ms")

    elif step_type == "activity":
        models._required_string(step, "activity_id", path)
        if step.get("action", "on") not in {"on", "off", "toggle"}:
            raise ValueError(f"{path}.action must be on, off or toggle")


def _validate_step(step: dict[str, Any], path: str = "step") -> None:
    _original_validate_step(step, path)
    if step.get("type") in _NEW_STEP_TYPES:
        _validate_extended_step(step, path)


def _migrate_steps(items: Any) -> list[dict[str, Any]]:
    result = _original_migrate_steps(items)
    for step in result:
        if not isinstance(step, dict):
            continue
        step_type = step.get("type")
        if step_type == "set_variable":
            step.setdefault("source", "literal")
            step.setdefault("source_variable", "")
            step.setdefault("entity_id", "")
            step.setdefault("attribute", "state")
        elif step_type == "template":
            step.setdefault("output_type", "auto")
            step.setdefault("transform", "none")
        elif step_type == "choose":
            cases: list[dict[str, Any]] = []
            for index, case in enumerate(step.get("cases", []) or []):
                if not isinstance(case, dict):
                    continue
                migrated = dict(case)
                migrated.setdefault("name", f"Case {index + 1}")
                migrated.setdefault("operator", "eq")
                migrated["steps"] = _migrate_steps(migrated.get("steps", []))
                cases.append(migrated)
            step["cases"] = cases
            step["default_steps"] = _migrate_steps(step.get("default_steps", []))
        elif step_type == "wait_event":
            step.setdefault("filters", {})
            step.setdefault("timeout_ms", 30_000)
            step.setdefault("store_as", "")
            step.setdefault("on_timeout", "fail")
        elif step_type == "run_automation":
            step.setdefault("wait", True)
            step.setdefault("propagate_failure", True)
            step.setdefault("pass_variables", False)
        elif step_type == "stop_automation":
            step.setdefault("target", "current")
            step.setdefault("automation_id", "")
            step.setdefault("require_running", False)
        elif step_type == "command_sequence":
            step.setdefault("mode", "commands")
            step.setdefault("macro_id", "")
            commands: list[dict[str, Any]] = []
            for command in step.get("commands", []) or []:
                if not isinstance(command, dict):
                    continue
                migrated = dict(command)
                migrated.setdefault("params", {})
                migrated.setdefault("delay_ms", 0)
                commands.append(migrated)
            step["commands"] = commands
        elif step_type == "activity":
            step.setdefault("action", "on")
    return result


def _collect_entity_ids(triggers: Any, *step_groups: Any) -> list[str]:
    result = list(_original_collect_entity_ids(triggers, *step_groups))
    seen = set(result)

    def add(value: Any) -> None:
        if isinstance(value, str) and value.strip() and not value.startswith("$entity:") and value not in seen:
            seen.add(value)
            result.append(value)

    def walk(items: Any) -> None:
        for step in items or []:
            if not isinstance(step, dict):
                continue
            step_type = step.get("type")
            if step_type == "set_variable" and step.get("source") == "entity":
                add(step.get("entity_id"))
            elif step_type == "activity":
                add(step.get("activity_id"))
            elif step_type == "command_sequence":
                if step.get("mode", "commands") == "macro":
                    add(step.get("macro_id"))
                else:
                    for command in step.get("commands", []) or []:
                        if isinstance(command, dict):
                            add(command.get("entity_id"))
            elif step_type == "choose":
                for case in step.get("cases", []) or []:
                    if isinstance(case, dict):
                        walk(case.get("steps", []))
                walk(step.get("default_steps", []))

            for key in ("then", "else", "failure_steps", "match_steps", "timeout_steps"):
                walk(step.get(key, []))
            if step_type == "parallel":
                for branch in step.get("branches", []) or []:
                    if isinstance(branch, dict):
                        walk(branch.get("steps", []))

    for group in step_groups:
        walk(group)
    return result


def install_model_extensions() -> None:
    """Install the v2 sequence-step extensions exactly once."""

    global _INSTALLED
    if _INSTALLED:
        return
    models.VALID_STEP_TYPES.update(_NEW_STEP_TYPES)
    models.validate_step = _validate_step
    models.migrate_steps = _migrate_steps
    models.collect_entity_ids = _collect_entity_ids
    _INSTALLED = True
