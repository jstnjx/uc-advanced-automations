"""Safe, deterministic automation execution engine."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, time
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import aiohttp

from .core_client import CoreApiError, CoreClient
from .models import Automation

_LOG = logging.getLogger(__name__)
_MISSING = object()


@dataclass(slots=True)
class LogEntry:
    sequence: int
    timestamp: str
    level: str
    run_id: str
    automation_id: str
    message: str


@dataclass(slots=True)
class RunResult:
    accepted: bool
    run_id: str | None
    reason: str | None = None


class AutomationEngine:
    """Execute sequential, conditional and wait-based automation flows."""

    def __init__(self, core: CoreClient, timezone_provider) -> None:
        self.core = core
        self._timezone_provider = timezone_provider
        self._running: dict[str, set[asyncio.Task[None]]] = {}
        self._logs: deque[LogEntry] = deque(maxlen=1000)
        self._log_sequence = 0
        self._http_session: aiohttp.ClientSession | None = None

    def running_count(self) -> int:
        return sum(len(tasks) for tasks in self._running.values())

    def logs_after(self, sequence: int = 0) -> list[dict[str, Any]]:
        return [asdict(entry) for entry in self._logs if entry.sequence > sequence]

    def start(self, automation: Automation, source: str = "unknown") -> RunResult:
        active = self._running.setdefault(automation.id, set())
        active = {task for task in active if not task.done()}
        self._running[automation.id] = active

        if automation.mode == "single" and active:
            self._write_log("warning", "-", automation.id, "Run ignored: automation is already active")
            return RunResult(False, None, "Automation is already running")

        run_id = str(uuid4())
        task = asyncio.create_task(self._run(automation, run_id, source), name=f"automation-{automation.id}")
        active.add(task)
        task.add_done_callback(lambda completed: self._remove_task(automation.id, completed))
        return RunResult(True, run_id)

    async def close(self) -> None:
        tasks = [task for group in self._running.values() for task in group]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._http_session:
            await self._http_session.close()
            self._http_session = None

    async def evaluate_group(self, group: dict[str, Any]) -> bool:
        conditions = group.get("conditions", [])
        results = [await self.evaluate_condition(condition) for condition in conditions]
        return all(results) if group.get("mode", "all") == "all" else any(results)

    async def evaluate_condition(self, condition: dict[str, Any]) -> bool:
        kind = condition.get("kind", "entity")
        if kind == "time":
            return self._evaluate_time_condition(condition)

        entity = await self.core.get_entity(condition["entity_id"])
        actual = _get_path(entity.get("attributes", {}), condition.get("attribute", "state"))
        return compare_values(actual, condition.get("operator", "eq"), condition.get("value"))

    async def _run(self, automation: Automation, run_id: str, source: str) -> None:
        self._write_log("info", run_id, automation.id, f"Started from {source}")
        try:
            await self._execute_steps(automation.steps, automation, run_id)
        except asyncio.CancelledError:
            self._write_log("warning", run_id, automation.id, "Cancelled")
            raise
        except Exception as err:
            _LOG.exception("Automation %s failed", automation.id)
            self._write_log("error", run_id, automation.id, f"Failed: {err}")
        else:
            self._write_log("success", run_id, automation.id, "Completed")

    async def _execute_steps(self, steps: list[dict[str, Any]], automation: Automation, run_id: str) -> None:
        for index, step in enumerate(steps, start=1):
            try:
                await self._execute_step(step, automation, run_id, index)
            except Exception as err:
                if step.get("continue_on_error", False):
                    self._write_log(
                        "warning",
                        run_id,
                        automation.id,
                        f"Step {index} failed but execution continues: {err}",
                    )
                    continue
                raise

    async def _execute_step(
        self,
        step: dict[str, Any],
        automation: Automation,
        run_id: str,
        index: int,
    ) -> None:
        step_type = step["type"]

        if step_type == "command":
            entity_id = step["entity_id"]
            command_id = step["cmd_id"]
            self._write_log("info", run_id, automation.id, f"Step {index}: {entity_id} → {command_id}")
            await self.core.execute_entity_command(entity_id, command_id, step.get("params") or None)
            return

        if step_type == "delay":
            milliseconds = float(step["milliseconds"])
            self._write_log("info", run_id, automation.id, f"Step {index}: wait {milliseconds:g} ms")
            await asyncio.sleep(milliseconds / 1000)
            return

        if step_type == "condition":
            result = await self.evaluate_group(step)
            branch_name = "then" if result else "else"
            self._write_log("info", run_id, automation.id, f"Step {index}: condition is {result}")
            await self._execute_steps(step.get(branch_name, []), automation, run_id)
            return

        if step_type == "wait":
            timeout = float(step.get("timeout_ms", 30_000)) / 1000
            interval = float(step.get("interval_ms", 500)) / 1000
            self._write_log("info", run_id, automation.id, f"Step {index}: wait until condition")
            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                if await self.evaluate_group(step):
                    return
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError(f"Wait condition timed out after {timeout:g} seconds")
                await asyncio.sleep(interval)

        if step_type == "http":
            await self._execute_http(step, automation, run_id, index)
            return

        if step_type == "log":
            self._write_log(step.get("level", "info"), run_id, automation.id, step["message"])
            return

        raise ValueError(f"Unknown step type: {step_type}")

    async def _execute_http(
        self,
        step: dict[str, Any],
        automation: Automation,
        run_id: str,
        index: int,
    ) -> None:
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        method = str(step.get("method", "POST")).upper()
        url = step["url"]
        timeout = aiohttp.ClientTimeout(total=float(step.get("timeout_seconds", 10)))
        self._write_log("info", run_id, automation.id, f"Step {index}: HTTP {method} {url}")

        kwargs: dict[str, Any] = {
            "headers": step.get("headers") or {},
            "timeout": timeout,
        }
        body = step.get("body")
        if body is not None:
            if isinstance(body, (dict, list)):
                kwargs["json"] = body
            else:
                kwargs["data"] = str(body)

        async with self._http_session.request(method, url, **kwargs) as response:
            minimum = int(step.get("status_min", 200))
            maximum = int(step.get("status_max", 299))
            if not minimum <= response.status <= maximum:
                text = (await response.text())[:300]
                raise RuntimeError(f"HTTP {response.status}: {text}")

    def _evaluate_time_condition(self, condition: dict[str, Any]) -> bool:
        try:
            zone = ZoneInfo(self._timezone_provider())
        except Exception:
            zone = ZoneInfo("UTC")
        now = datetime.now(zone)
        weekdays = condition.get("weekdays", list(range(7)))
        if now.weekday() not in weekdays:
            inside = False
        else:
            start = time.fromisoformat(condition["start"])
            end = time.fromisoformat(condition["end"])
            current = now.time().replace(tzinfo=None)
            inside = start <= current <= end if start <= end else current >= start or current <= end
        return inside if condition.get("operator", "between") == "between" else not inside

    def _write_log(self, level: str, run_id: str, automation_id: str, message: str) -> None:
        self._log_sequence += 1
        self._logs.append(
            LogEntry(
                sequence=self._log_sequence,
                timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
                level=level,
                run_id=run_id,
                automation_id=automation_id,
                message=message,
            )
        )

    def _remove_task(self, automation_id: str, task: asyncio.Task[None]) -> None:
        group = self._running.get(automation_id)
        if group is not None:
            group.discard(task)
            if not group:
                self._running.pop(automation_id, None)


def _get_path(data: Any, path: str) -> Any:
    current = data
    if path == "":
        return current
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
            current = current[int(segment)]
        else:
            return _MISSING
    return current


def compare_values(actual: Any, operator: str, expected: Any = None) -> bool:
    """Compare condition values without evaluating arbitrary expressions."""

    if operator == "exists":
        return actual is not _MISSING
    if operator == "not_exists":
        return actual is _MISSING
    if actual is _MISSING:
        return False
    if operator == "truthy":
        return bool(actual)
    if operator == "falsy":
        return not bool(actual)

    left, right = _coerce_pair(actual, expected)
    if operator == "eq":
        return left == right
    if operator == "ne":
        return left != right
    if operator == "gt":
        return left > right
    if operator == "gte":
        return left >= right
    if operator == "lt":
        return left < right
    if operator == "lte":
        return left <= right
    if operator == "contains":
        return right in left
    if operator == "not_contains":
        return right not in left
    if operator == "in":
        return left in right
    if operator == "not_in":
        return left not in right
    raise ValueError(f"Unsupported operator: {operator}")


def _coerce_pair(left: Any, right: Any) -> tuple[Any, Any]:
    if isinstance(left, bool) or isinstance(right, bool):
        return left, right
    try:
        return float(left), float(right)
    except (TypeError, ValueError):
        return left, right
