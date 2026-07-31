"""Safe automation execution engine with persistent history and execution policies."""

from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime, time
from typing import Any, Awaitable, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

import aiohttp

from .core_client import CoreClient
from .database import AutomationDatabase
from .models import Automation

_LOG = logging.getLogger(__name__)
_MISSING = object()


class _AutomationStopped(Exception):
    """Stop the remaining sequence without marking the run failed."""


class _RollbackRequested(Exception):
    """Request the automation-level rollback sequence."""


class AutomationEngine:
    """Execute sequential, conditional, parallel, and recovery-aware flows."""

    def __init__(
        self,
        core: CoreClient,
        timezone_provider: Callable[[], str],
        database: AutomationDatabase | None = None,
    ) -> None:
        self.core = core
        self._owns_database = database is None
        self.database = database or AutomationDatabase()
        self._timezone_provider = timezone_provider
        self._running: dict[str, set[asyncio.Task[None]]] = {}
        self._http_session: aiohttp.ClientSession | None = None
        self._start_listeners: list[Callable[[Automation, str, str], Any | Awaitable[Any]]] = []
        self._completion_listeners: list[
            Callable[[Automation, str, str, str | None], Any | Awaitable[Any]]
        ] = []

    def add_start_listener(self, callback: Callable[[Automation, str, str], Any | Awaitable[Any]]) -> None:
        self._start_listeners.append(callback)

    def add_completion_listener(
        self,
        callback: Callable[[Automation, str, str, str | None], Any | Awaitable[Any]],
    ) -> None:
        self._completion_listeners.append(callback)

    def running_count(self) -> int:
        return sum(len(tasks) for tasks in self._running.values())

    def logs_after(self, sequence: int = 0) -> list[dict[str, Any]]:
        return self.database.logs_after(sequence)

    def start(self, automation: Automation, source: str = "unknown") -> "RunResult":
        active = {task for task in self._running.setdefault(automation.id, set()) if not task.done()}
        self._running[automation.id] = active
        if automation.mode == "single" and active:
            self._write_log("warning", "-", automation.id, "Run ignored: automation is already active")
            return RunResult(False, None, "Automation is already running")
        if automation.mode == "replace" and active:
            for task in tuple(active):
                task.cancel()
            self._write_log("info", "-", automation.id, "Replacing active run")

        run_id = str(uuid4())
        self.database.start_run(run_id, automation.id, automation.name, source)
        self._notify_started(automation, run_id, source)
        task = asyncio.create_task(self._run(automation, run_id, source), name=f"automation-{automation.id}")
        active.add(task)
        task.add_done_callback(lambda completed: self._remove_task(automation.id, completed))
        return RunResult(True, run_id)

    def _notify_started(self, automation: Automation, run_id: str, source: str) -> None:
        self._notify(self._start_listeners, automation, run_id, source)

    def _notify_completed(
        self, automation: Automation, run_id: str, status: str, error: str | None
    ) -> None:
        self._notify(self._completion_listeners, automation, run_id, status, error)

    @staticmethod
    def _notify(callbacks: list[Callable[..., Any]], *args: Any) -> None:
        for callback in tuple(callbacks):
            try:
                result = callback(*args)
                if inspect.isawaitable(result):
                    asyncio.create_task(result)
            except Exception:  # pragma: no cover
                _LOG.exception("Automation listener failed")

    async def close(self) -> None:
        tasks = [task for group in self._running.values() for task in group]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._http_session:
            await self._http_session.close()
            self._http_session = None
        if self._owns_database:
            self.database.close()
            self._owns_database = False

    async def evaluate_group(self, group: dict[str, Any]) -> bool:
        conditions = group.get("conditions", [])
        results = [await self.evaluate_condition(condition) for condition in conditions]
        return all(results) if group.get("mode", "all") == "all" else any(results)

    async def evaluate_condition(self, condition: dict[str, Any]) -> bool:
        if condition.get("kind", "entity") == "time":
            return self._evaluate_time_condition(condition)
        entity = await self.core.get_entity(condition["entity_id"])
        actual = _get_path(entity.get("attributes", {}), condition.get("attribute", "state"))
        return compare_values(actual, condition.get("operator", "eq"), condition.get("value"))

    async def _run(self, automation: Automation, run_id: str, source: str) -> None:
        self._write_log("info", run_id, automation.id, f"Started from {source}")
        triggered_at = asyncio.get_running_loop().time()
        status = "success"
        error: str | None = None
        try:
            if automation.max_runtime_ms:
                async with asyncio.timeout(automation.max_runtime_ms / 1000):
                    await self._execute_steps(automation.steps, automation, run_id, triggered_at)
            else:
                await self._execute_steps(automation.steps, automation, run_id, triggered_at)
        except _AutomationStopped as stopped:
            status = "stopped"
            self._write_log("info", run_id, automation.id, str(stopped))
            self._write_log("success", run_id, automation.id, "Completed")
        except asyncio.CancelledError:
            status = "cancelled"
            error = "Cancelled"
            self._write_log("warning", run_id, automation.id, "Cancelled")
            await self._run_cleanup(automation.cancellation_steps, automation, run_id, triggered_at, "cancellation cleanup")
            raise
        except Exception as err:  # noqa: BLE001 - execution boundary
            status = "failure"
            error = str(err)
            _LOG.exception("Automation %s failed", automation.id)
            self._write_log("error", run_id, automation.id, f"Failed: {err}")
            if automation.rollback_steps:
                await self._run_cleanup(automation.rollback_steps, automation, run_id, triggered_at, "rollback")
        else:
            self._write_log("success", run_id, automation.id, "Completed")
        finally:
            self.database.set_current_step(run_id, None)
            self.database.finish_run(run_id, status, error)
            self._notify_completed(automation, run_id, status, error)

    async def _run_cleanup(
        self,
        steps: list[dict[str, Any]],
        automation: Automation,
        run_id: str,
        triggered_at: float,
        label: str,
    ) -> None:
        if not steps:
            return
        self._write_log("info", run_id, automation.id, f"Starting {label}")
        task = asyncio.create_task(
            self._execute_steps(steps, automation, run_id, triggered_at, prefix=label.title()),
            name=f"automation-{automation.id}-{label.replace(' ', '-')}",
        )
        try:
            await asyncio.shield(task)
        except Exception as cleanup_error:  # noqa: BLE001
            self._write_log("error", run_id, automation.id, f"{label.title()} failed: {cleanup_error}")

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
            self.database.set_current_step(run_id, f"{path}: {step_label(step)}")
            await self._execute_step_with_policy(step, automation, run_id, path, triggered_at)

    async def _execute_step_with_policy(
        self,
        step: dict[str, Any],
        automation: Automation,
        run_id: str,
        path: str,
        triggered_at: float,
    ) -> None:
        retries = int(step.get("retry_count", 0))
        delay_ms = float(step.get("retry_delay_ms", 1000))
        backoff = step.get("retry_backoff", "fixed")
        for attempt in range(retries + 1):
            try:
                timeout_ms = float(step.get("execution_timeout_ms", 0))
                if timeout_ms:
                    await asyncio.wait_for(
                        self._execute_step(step, automation, run_id, path, triggered_at),
                        timeout=timeout_ms / 1000,
                    )
                else:
                    await self._execute_step(step, automation, run_id, path, triggered_at)
                return
            except (asyncio.CancelledError, _AutomationStopped):
                raise
            except Exception as err:  # noqa: BLE001
                if attempt < retries:
                    wait_ms = delay_ms * (2**attempt if backoff == "exponential" else 1)
                    self._write_log(
                        "warning", run_id, automation.id,
                        f"{path} failed ({err}); retry {attempt + 1}/{retries} in {wait_ms:g} ms",
                    )
                    await asyncio.sleep(wait_ms / 1000)
                    continue
                action = step.get("failure_action", "fail")
                if step.get("continue_on_error") and "failure_action" not in step:
                    action = "continue"
                if action == "continue":
                    self._write_log("warning", run_id, automation.id, f"{path} failed; continuing: {err}")
                    return
                if action == "branch":
                    self._write_log("warning", run_id, automation.id, f"{path} failed; running failure branch")
                    await self._execute_steps(
                        step.get("failure_steps", []), automation, run_id, triggered_at,
                        prefix=f"{path} failure",
                    )
                    return
                if action == "rollback":
                    raise _RollbackRequested(f"{path} requested rollback: {err}") from err
                raise

    async def _execute_step(
        self,
        step: dict[str, Any],
        automation: Automation,
        run_id: str,
        path: str,
        triggered_at: float,
    ) -> None:
        step_type = step["type"]
        if step_type == "command":
            entity_id, command_id = step["entity_id"], step["cmd_id"]
            self._write_log("info", run_id, automation.id, f"{path}: {entity_id} → {command_id}")
            await self.core.execute_entity_command(entity_id, command_id, step.get("params") or None)
            return
        if step_type == "delay":
            milliseconds = float(step["milliseconds"])
            self._write_log("info", run_id, automation.id, f"{path}: wait {milliseconds:g} ms")
            await asyncio.sleep(milliseconds / 1000)
            return
        if step_type == "condition":
            result = await self.evaluate_group(step)
            branch_name = "then" if result else "else"
            self._write_log("info", run_id, automation.id, f"{path}: condition is {result}")
            await self._execute_steps(
                step.get(branch_name, []), automation, run_id, triggered_at,
                prefix=f"{path} {branch_name.title()}",
            )
            return
        if step_type == "wait":
            await self._execute_wait(step, automation, run_id, path, triggered_at)
            return
        if step_type == "parallel":
            await self._execute_parallel(step, automation, run_id, path, triggered_at)
            return
        if step_type == "http":
            await self._execute_http(step, automation, run_id, path)
            return
        if step_type == "log":
            self._write_log(step.get("level", "info"), run_id, automation.id, step["message"])
            return
        raise ValueError(f"Unknown step type: {step_type}")

    async def _execute_wait(
        self,
        step: dict[str, Any],
        automation: Automation,
        run_id: str,
        path: str,
        triggered_at: float,
    ) -> None:
        timeout = float(step.get("timeout_ms", 30_000)) / 1000
        interval = float(step.get("interval_ms", 500)) / 1000
        loop = asyncio.get_running_loop()
        reference = step.get("time_reference", "step")
        deadline = (triggered_at if reference == "trigger" else loop.time()) + timeout
        self._write_log(
            "info", run_id, automation.id,
            f"{path}: wait up to {timeout:g} seconds from {reference} for condition",
        )
        matched = False
        while loop.time() < deadline:
            if await self.evaluate_group(step):
                matched = True
                break
            await asyncio.sleep(min(interval, max(0.01, deadline - loop.time())))
        action = step.get("on_match", "continue") if matched else step.get("on_timeout", "fail")
        branch_key = "match_steps" if matched else "timeout_steps"
        outcome = "matched" if matched else "timed out"
        self._write_log("info", run_id, automation.id, f"{path}: condition {outcome}; action={action}")
        if action == "continue":
            if not matched and reference == "trigger":
                self._write_log("info", run_id, automation.id, f"{path}: trigger timeframe elapsed; continuing")
            return
        if action == "stop":
            if matched and reference == "trigger":
                self._write_log("info", run_id, automation.id, f"{path}: condition matched; remaining sequence skipped")
            raise _AutomationStopped(f"{path}: condition {outcome}; sequence stopped successfully")
        if action == "fail":
            raise TimeoutError(f"{path}: wait condition timed out after {timeout:g} seconds")
        if action == "branch":
            await self._execute_steps(
                step.get(branch_key, []), automation, run_id, triggered_at,
                prefix=f"{path} {'Match' if matched else 'Timeout'}",
            )
            return
        raise ValueError(f"Unsupported wait outcome: {action}")

    async def _execute_parallel(
        self,
        step: dict[str, Any],
        automation: Automation,
        run_id: str,
        path: str,
        triggered_at: float,
    ) -> None:
        branches = step.get("branches", [])
        tasks = [
            asyncio.create_task(
                self._execute_steps(
                    branch.get("steps", []), automation, run_id, triggered_at,
                    prefix=f"{path} · {branch.get('name', f'Branch {index + 1}')}",
                )
            )
            for index, branch in enumerate(branches)
        ]
        self._write_log("info", run_id, automation.id, f"{path}: started {len(tasks)} parallel branches")
        if step.get("wait_for", "all") == "any":
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            first = next(iter(done))
            await first
        else:
            await asyncio.gather(*tasks)

    async def _execute_http(
        self,
        step: dict[str, Any],
        automation: Automation,
        run_id: str,
        path: str,
    ) -> None:
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        method, url = str(step.get("method", "POST")).upper(), step["url"]
        timeout = aiohttp.ClientTimeout(total=float(step.get("timeout_seconds", 10)))
        self._write_log("info", run_id, automation.id, f"{path}: HTTP {method} {url}")
        kwargs: dict[str, Any] = {"headers": step.get("headers") or {}, "timeout": timeout}
        body = step.get("body")
        if body is not None:
            kwargs["json" if isinstance(body, (dict, list)) else "data"] = body if isinstance(body, (dict, list)) else str(body)
        async with self._http_session.request(method, url, **kwargs) as response:
            minimum, maximum = int(step.get("status_min", 200)), int(step.get("status_max", 299))
            if not minimum <= response.status <= maximum:
                raise RuntimeError(f"HTTP {response.status}: {(await response.text())[:300]}")

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
            start, end = time.fromisoformat(condition["start"]), time.fromisoformat(condition["end"])
            current = now.time().replace(tzinfo=None)
            inside = start <= current <= end if start <= end else current >= start or current <= end
        return inside if condition.get("operator", "between") == "between" else not inside

    def _write_log(self, level: str, run_id: str, automation_id: str, message: str) -> None:
        self.database.log_event(level, run_id, automation_id, message)

    def _remove_task(self, automation_id: str, task: asyncio.Task[None]) -> None:
        group = self._running.get(automation_id)
        if group is not None:
            group.discard(task)
            if not group:
                self._running.pop(automation_id, None)


class RunResult:
    def __init__(self, accepted: bool, run_id: str | None, reason: str | None = None) -> None:
        self.accepted, self.run_id, self.reason = accepted, run_id, reason


def step_label(step: dict[str, Any]) -> str:
    labels = {
        "command": f"Entity command {step.get('cmd_id', '')}",
        "delay": "Delay",
        "condition": "If / else",
        "wait": "Wait for condition",
        "parallel": "Parallel group",
        "http": "HTTP request",
        "log": "Log message",
    }
    return labels.get(step.get("type"), str(step.get("type", "Step")))


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
    if operator == "exists": return actual is not _MISSING
    if operator == "not_exists": return actual is _MISSING
    if actual is _MISSING: return False
    if operator == "truthy": return bool(actual)
    if operator == "falsy": return not bool(actual)
    left, right = _coerce_pair(actual, expected)
    if operator == "eq": return left == right
    if operator == "ne": return left != right
    if operator == "gt": return left > right
    if operator == "gte": return left >= right
    if operator == "lt": return left < right
    if operator == "lte": return left <= right
    if operator == "contains": return right in left
    if operator == "not_contains": return right not in left
    if operator == "in": return left in right
    if operator == "not_in": return left not in right
    raise ValueError(f"Unsupported operator: {operator}")


def _coerce_pair(left: Any, right: Any) -> tuple[Any, Any]:
    if isinstance(left, bool) or isinstance(right, bool):
        return left, right
    try:
        return float(left), float(right)
    except (TypeError, ValueError):
        return left, right
