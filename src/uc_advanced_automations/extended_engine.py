"""Extended v2 sequence executor.

The existing AutomationEngine remains the compatibility base. This subclass adds
run-scoped values and orchestration steps while preserving the existing
retry/failure/rollback behavior for every step.
"""

from __future__ import annotations

import asyncio
import contextvars
import copy
import json
import re
from collections.abc import Callable
from typing import Any

from .core_client import CoreClient
from .database import AutomationDatabase
from .engine import AutomationEngine, RunResult, _AutomationStopped, _MISSING, _get_path, compare_values
from .models import Automation

_TEMPLATE_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


class ExtendedAutomationEngine(AutomationEngine):
    """AutomationEngine with v2 orchestration and run-value steps."""

    def __init__(
        self,
        core: CoreClient,
        timezone_provider: Callable[[], str],
        database: AutomationDatabase | None = None,
        automation_resolver: Callable[[str], Automation | None] | None = None,
    ) -> None:
        super().__init__(core, timezone_provider, database)
        self._automation_resolver = automation_resolver or (lambda _automation_id: None)
        self._initial_contexts: dict[str, dict[str, Any]] = {}
        self._run_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
            "advanced_automations_run_context", default=None
        )

    def start(
        self,
        automation: Automation,
        source: str = "unknown",
        *,
        context: dict[str, Any] | None = None,
    ) -> RunResult:
        result = super().start(automation, source)
        if result.accepted and result.run_id:
            self._initial_contexts[result.run_id] = copy.deepcopy(context or {})
        return result

    async def _run(self, automation: Automation, run_id: str, source: str) -> None:
        initial = self._initial_contexts.pop(run_id, {})
        variables = initial.get("variables")
        if not isinstance(variables, dict):
            variables = {}
        chain = initial.get("automation_chain")
        if not isinstance(chain, list):
            chain = []
        chain = [str(item) for item in chain if item]
        if not chain or chain[-1] != automation.id:
            chain.append(automation.id)
        context = {
            "variables": copy.deepcopy(variables),
            "automation_chain": chain,
            "source": source,
            "automation": automation,
            "run_id": run_id,
        }
        token = self._run_context.set(context)
        try:
            await super()._run(automation, run_id, source)
        finally:
            self._run_context.reset(token)

    def _context(self) -> dict[str, Any]:
        context = self._run_context.get()
        if context is None:
            raise RuntimeError("Sequence step is not running inside an automation context")
        return context

    def variables(self) -> dict[str, Any]:
        """Return a defensive copy of variables for the current run."""
        return copy.deepcopy(self._context()["variables"])

    async def evaluate_condition(self, condition: dict[str, Any]) -> bool:
        if condition.get("kind", "entity") == "time":
            return self._evaluate_time_condition(condition)
        entity = await self.core.get_entity(condition["entity_id"])
        actual = _get_path(entity.get("attributes", {}), condition.get("attribute", "state"))
        expected = self._render_value(condition.get("value"))
        return compare_values(actual, condition.get("operator", "eq"), expected)

    async def _execute_steps(
        self,
        steps: list[dict[str, Any]],
        automation: Automation,
        run_id: str,
        triggered_at: float,
        *,
        prefix: str = "Step",
    ) -> None:
        for index, step in enumerate(steps, start=1):
            path = f"{prefix} {index}"
            self.database.set_current_step(run_id, f"{path}: {extended_step_label(step)}")
            await self._execute_step_with_policy(step, automation, run_id, path, triggered_at)

    async def _execute_step(
        self,
        step: dict[str, Any],
        automation: Automation,
        run_id: str,
        path: str,
        triggered_at: float,
    ) -> None:
        step_type = step["type"]

        if step_type == "set_variable":
            await self._execute_set_variable(step, automation, run_id, path)
            return
        if step_type == "template":
            self._execute_template(step, automation, run_id, path)
            return
        if step_type == "choose":
            await self._execute_choose(step, automation, run_id, path, triggered_at)
            return
        if step_type == "wait_event":
            await self._execute_wait_event(step, automation, run_id, path)
            return
        if step_type == "run_automation":
            await self._execute_run_automation(step, automation, run_id, path)
            return
        if step_type == "stop_automation":
            await self._execute_stop_automation(step, automation, run_id, path)
            return
        if step_type == "command_sequence":
            await self._execute_command_sequence(step, automation, run_id, path)
            return
        if step_type == "activity":
            await self._execute_activity(step, automation, run_id, path)
            return

        if step_type == "command":
            rendered = dict(step)
            rendered["params"] = self._render_value(step.get("params") or {})
            await super()._execute_step(rendered, automation, run_id, path, triggered_at)
            return
        if step_type == "http":
            rendered = dict(step)
            rendered["url"] = str(self._render_value(step.get("url", "")))
            rendered["headers"] = self._render_value(step.get("headers") or {})
            if "body" in step:
                rendered["body"] = self._render_value(step.get("body"))
            await super()._execute_step(rendered, automation, run_id, path, triggered_at)
            return
        if step_type == "log":
            rendered = dict(step)
            rendered["message"] = str(self._render_value(step.get("message", "")))
            await super()._execute_step(rendered, automation, run_id, path, triggered_at)
            return

        await super()._execute_step(step, automation, run_id, path, triggered_at)

    async def _execute_set_variable(self, step: dict[str, Any], automation: Automation, run_id: str, path: str) -> None:
        source = step.get("source", "literal")
        if source == "literal":
            value = self._render_value(copy.deepcopy(step.get("value")))
        elif source == "variable":
            source_name = str(step["source_variable"])
            variables = self._context()["variables"]
            if source_name not in variables:
                raise KeyError(f"Run variable '{source_name}' is not set")
            value = copy.deepcopy(variables[source_name])
        elif source == "entity":
            entity = await self.core.get_entity(step["entity_id"])
            value = _get_path(entity.get("attributes", {}), step.get("attribute", "state"))
            if value is _MISSING:
                raise KeyError(
                    f"Entity attribute '{step.get('attribute', 'state')}' does not exist on {step['entity_id']}"
                )
            value = copy.deepcopy(value)
        else:
            raise ValueError(f"Unsupported variable source: {source}")

        self._context()["variables"][step["name"]] = value
        self._write_log("info", run_id, automation.id, f"{path}: set variable {step['name']}")

    def _execute_template(self, step: dict[str, Any], automation: Automation, run_id: str, path: str) -> None:
        value = self._render_template(str(step["template"]))
        value = self._transform_value(value, step.get("transform", "none"))
        value = self._coerce_output(value, step.get("output_type", "auto"))
        self._context()["variables"][step["name"]] = value
        self._write_log("info", run_id, automation.id, f"{path}: rendered value into {step['name']}")

    async def _execute_choose(
        self,
        step: dict[str, Any],
        automation: Automation,
        run_id: str,
        path: str,
        triggered_at: float,
    ) -> None:
        actual = self._render_template(str(step["expression"]))
        selected: dict[str, Any] | None = None
        for case in step.get("cases", []):
            operator = case.get("operator", "eq")
            expected = self._render_value(case.get("value"))
            if compare_values(actual, operator, expected):
                selected = case
                break

        if selected is not None:
            name = selected.get("name", "Case")
            self._write_log("info", run_id, automation.id, f"{path}: choose matched {name}")
            await self._execute_steps(
                selected.get("steps", []), automation, run_id, triggered_at, prefix=f"{path} · {name}"
            )
            return

        self._write_log("info", run_id, automation.id, f"{path}: choose used default branch")
        await self._execute_steps(
            step.get("default_steps", []), automation, run_id, triggered_at, prefix=f"{path} · Default"
        )

    async def _execute_wait_event(self, step: dict[str, Any], automation: Automation, run_id: str, path: str) -> None:
        event_name = str(step["event"])
        timeout = float(step.get("timeout_ms", 30_000)) / 1000
        filters = self._render_value(step.get("filters") or {})
        if not isinstance(filters, dict):
            raise ValueError("Wait event filters must render to an object")

        await self.core.connect()
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()

        async def callback(data: dict[str, Any]) -> None:
            if future.done():
                return
            for key, expected in filters.items():
                actual = _get_path(data, str(key))
                if actual is _MISSING or not compare_values(actual, "eq", expected):
                    return
            future.set_result(copy.deepcopy(data))

        self.core.add_event_listener(event_name, callback)
        self._write_log(
            "info", run_id, automation.id,
            f"{path}: waiting up to {timeout:g}s for Core event {event_name}",
        )
        try:
            data = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as err:
            action = step.get("on_timeout", "fail")
            self._write_log("warning", run_id, automation.id, f"{path}: event wait timed out; action={action}")
            if action == "continue":
                return
            if action == "stop":
                raise _AutomationStopped(f"{path}: event wait timed out; sequence stopped successfully")
            raise TimeoutError(f"{path}: event '{event_name}' was not received within {timeout:g}s") from err
        finally:
            self.core.remove_event_listener(event_name, callback)

        store_as = str(step.get("store_as", "")).strip()
        if store_as:
            self._context()["variables"][store_as] = data
        self._write_log("info", run_id, automation.id, f"{path}: received Core event {event_name}")

    async def _execute_run_automation(self, step: dict[str, Any], automation: Automation, run_id: str, path: str) -> None:
        target_id = str(step["automation_id"])
        target = self._automation_resolver(target_id)
        if target is None:
            raise ValueError(f"Automation '{target_id}' does not exist")
        if not target.enabled:
            raise ValueError(f"Automation '{target.name}' is disabled")

        context = self._context()
        chain = list(context["automation_chain"])
        if target.id in chain:
            names = " -> ".join([*chain, target.id])
            raise RuntimeError(f"Recursive automation call detected: {names}")

        child_context = {
            "automation_chain": [*chain, target.id],
            "variables": copy.deepcopy(context["variables"]) if step.get("pass_variables", False) else {},
        }
        result = self.start(target, source=f"Automation: {automation.name}", context=child_context)
        if not result.accepted or not result.run_id:
            raise RuntimeError(result.reason or f"Automation '{target.name}' could not be started")

        self._write_log("info", run_id, automation.id, f"{path}: started automation {target.name}")
        if not step.get("wait", True):
            return

        record = await self._wait_for_persisted_run(target.id, result.run_id)
        status = str(record.get("status", "failure"))
        self._write_log(
            "info", run_id, automation.id,
            f"{path}: automation {target.name} finished with {status}",
        )
        if step.get("propagate_failure", True) and status in {"failure", "cancelled"}:
            detail = record.get("error") or status
            raise RuntimeError(f"Automation '{target.name}' finished with {status}: {detail}")

    async def _execute_stop_automation(self, step: dict[str, Any], automation: Automation, run_id: str, path: str) -> None:
        target = step.get("target", "current")
        target_id = automation.id if target == "current" else str(step.get("automation_id", ""))
        if target_id == automation.id:
            raise _AutomationStopped(f"{path}: automation stopped by sequence")

        active = {task for task in self._running.get(target_id, set()) if not task.done()}
        if not active:
            if step.get("require_running", False):
                raise RuntimeError(f"Automation '{target_id}' is not currently running")
            self._write_log("info", run_id, automation.id, f"{path}: target automation was not running")
            return

        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
        self._write_log(
            "info", run_id, automation.id,
            f"{path}: stopped {len(active)} run(s) of automation {target_id}",
        )

    async def _execute_command_sequence(self, step: dict[str, Any], automation: Automation, run_id: str, path: str) -> None:
        if step.get("mode", "commands") == "macro":
            macro_id = str(step["macro_id"])
            self._write_log("info", run_id, automation.id, f"{path}: run macro {macro_id}")
            await self.core.execute_entity_command(macro_id, "macro.run")
            return

        commands = step.get("commands", [])
        self._write_log("info", run_id, automation.id, f"{path}: execute {len(commands)} commands")
        for index, command in enumerate(commands, start=1):
            entity_id = str(command["entity_id"])
            command_id = str(command["cmd_id"])
            params = self._render_value(command.get("params") or {})
            self._write_log("info", run_id, automation.id, f"{path}.{index}: {entity_id} -> {command_id}")
            await self.core.execute_entity_command(entity_id, command_id, params or None)
            delay_ms = float(command.get("delay_ms", 0))
            if delay_ms:
                await asyncio.sleep(delay_ms / 1000)

    async def _execute_activity(self, step: dict[str, Any], automation: Automation, run_id: str, path: str) -> None:
        activity_id = str(step["activity_id"])
        action = step.get("action", "on")
        if action == "toggle":
            entity = await self.core.get_entity(activity_id)
            state = str(_get_path(entity.get("attributes", {}), "state")).upper()
            action = "off" if state in {"ON", "RUNNING", "STARTING"} else "on"
        command = f"activity.{action}"
        self._write_log("info", run_id, automation.id, f"{path}: {activity_id} -> {command}")
        await self.core.execute_entity_command(activity_id, command)

    async def _wait_for_persisted_run(self, automation_id: str, run_id: str) -> dict[str, Any]:
        while True:
            summary = self.database.run_summary(automation_id, recent_limit=100)
            record = next(
                (item for item in summary.get("recent_runs", []) if item.get("run_id") == run_id),
                None,
            )
            if record is not None and record.get("status") != "running":
                return record
            await asyncio.sleep(0.05)

    def _render_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._render_template(value)
        if isinstance(value, list):
            return [self._render_value(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self._render_value(item) for key, item in value.items()}
        return copy.deepcopy(value)

    def _render_template(self, template: str) -> Any:
        matches = list(_TEMPLATE_RE.finditer(template))
        if not matches:
            return template
        if len(matches) == 1 and matches[0].start() == 0 and matches[0].end() == len(template):
            return copy.deepcopy(self._resolve_expression(matches[0].group(1)))

        pieces: list[str] = []
        offset = 0
        for match in matches:
            pieces.append(template[offset:match.start()])
            value = self._resolve_expression(match.group(1))
            pieces.append(self._stringify(value))
            offset = match.end()
        pieces.append(template[offset:])
        return "".join(pieces)

    def _resolve_expression(self, expression: str) -> Any:
        parts = [part.strip() for part in expression.split("|")]
        value = self._lookup_template_path(parts[0])
        for transform in parts[1:]:
            value = self._transform_value(value, transform)
        return value

    def _lookup_template_path(self, path: str) -> Any:
        context = self._context()
        variables = context["variables"]
        if path.startswith("vars."):
            value = _get_path(variables, path[5:])
        elif path.startswith("automation."):
            value = _get_path(
                context["automation"].model_dump(mode="json"),
                path[len("automation."):],
            )
        elif path == "automation":
            value = context["automation"].model_dump(mode="json")
        elif path.startswith("run."):
            value = _get_path({"id": context["run_id"], "source": context["source"]}, path[4:])
        elif path == "run":
            value = {"id": context["run_id"], "source": context["source"]}
        else:
            value = _get_path(variables, path)
        if value is _MISSING:
            raise KeyError(f"Template value '{path}' is not available")
        return value

    @staticmethod
    def _stringify(value: Any) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    @staticmethod
    def _transform_value(value: Any, transform: str) -> Any:
        transform = str(transform or "none").lower()
        if transform in {"", "none"}:
            return value
        if transform == "lower":
            return str(value).lower()
        if transform == "upper":
            return str(value).upper()
        if transform == "trim":
            return str(value).strip()
        if transform == "length":
            if not hasattr(value, "__len__"):
                raise ValueError("length transform requires a sized value")
            return len(value)
        if transform == "json":
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if transform == "int":
            return int(float(value))
        if transform == "float":
            return float(value)
        if transform == "bool":
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in {"true", "1", "yes", "on"}:
                return True
            if text in {"false", "0", "no", "off", ""}:
                return False
            raise ValueError(f"Cannot convert {value!r} to boolean")
        raise ValueError(f"Unsupported template transform: {transform}")

    @staticmethod
    def _coerce_output(value: Any, output_type: str) -> Any:
        output_type = str(output_type or "auto")
        if output_type == "auto":
            return value
        if output_type == "string":
            return ExtendedAutomationEngine._stringify(value)
        if output_type == "number":
            number = float(value)
            return int(number) if number.is_integer() else number
        if output_type == "boolean":
            return ExtendedAutomationEngine._transform_value(value, "bool")
        if output_type == "json":
            if isinstance(value, str):
                return json.loads(value)
            return copy.deepcopy(value)
        raise ValueError(f"Unsupported template output type: {output_type}")


def extended_step_label(step: dict[str, Any]) -> str:
    labels = {
        "set_variable": f"Set variable {step.get('name', '')}".strip(),
        "template": f"Template value -> {step.get('name', '')}".strip(),
        "choose": "Choose / switch",
        "wait_event": f"Wait for event {step.get('event', '')}".strip(),
        "run_automation": "Run automation",
        "stop_automation": "Stop automation",
        "command_sequence": "Run macro" if step.get("mode", "commands") == "macro" else "Command sequence",
        "activity": f"Activity {step.get('action', 'on')}",
    }
    if step.get("type") in labels:
        return labels[step["type"]]
    from .engine import step_label
    return step_label(step)
