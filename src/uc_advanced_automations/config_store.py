"""Atomic JSON configuration persistence with crash-safe recovery."""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

from pydantic import ValidationError

from .models import AppConfig, Automation, Settings
from .runtime import RuntimeEnvironment, detect_runtime

_LOG = logging.getLogger(__name__)


class ConfigStore:
    """Read, validate, migrate and atomically persist application configuration."""

    def __init__(
        self,
        data_dir: Path | None = None,
        runtime: RuntimeEnvironment | None = None,
    ) -> None:
        self.runtime = runtime or detect_runtime()
        self.data_dir = (data_dir or self.runtime.data_dir).expanduser().resolve()
        self.path = self.data_dir / "config.json"
        self._lock = RLock()
        self._config = self._default_config()
        self._recovery: dict[str, Any] = {
            "config_recovered": False,
            "config_backup": None,
            "config_error": None,
            "config_skipped_automations": 0,
        }
        self.load()

    @property
    def recovery_status(self) -> dict[str, Any]:
        """Return details about any startup configuration recovery."""
        with self._lock:
            return dict(self._recovery)

    def _default_config(self) -> AppConfig:
        return AppConfig(
            settings=Settings(
                core_url=self.runtime.default_core_url,
                web_host=self.runtime.web_host,
                web_port=self.runtime.web_port,
            )
        )

    def load(self) -> AppConfig:
        with self._lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            if not self.path.exists():
                self._config = self._default_config()
                self.save()
                return self.snapshot()

            raw: Any = None
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                raw_migrated = self._migrate_raw_reserved_web_port(raw)
                self._config = AppConfig.model_validate(raw)
                if raw_migrated or self._migrate_reserved_web_port():
                    self.save()
                return self.snapshot()
            except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError, TypeError) as err:
                backup = self._backup_invalid_config()
                self._config, skipped = self._salvage_config(raw)
                self._migrate_reserved_web_port()
                self._recovery = {
                    "config_recovered": True,
                    "config_backup": str(backup) if backup else None,
                    "config_error": f"{type(err).__name__}: {err}",
                    "config_skipped_automations": skipped,
                }
                _LOG.error(
                    "Invalid persisted configuration recovered: error=%s backup=%s skipped_automations=%d",
                    self._recovery["config_error"],
                    backup,
                    skipped,
                )
                self.save()
                return self.snapshot()

    def _migrate_raw_reserved_web_port(self, raw: Any) -> bool:
        """Normalize persisted legacy editor ports before strict validation."""

        if not isinstance(raw, dict):
            return False
        settings = raw.get("settings")
        if not isinstance(settings, dict):
            return False
        try:
            current = int(settings.get("web_port"))
        except (TypeError, ValueError):
            return False
        if current > 9200:
            return False
        settings["web_port"] = self.runtime.web_port
        _LOG.info(
            "Migrating persisted Integration API reserved editor port from %d to %d",
            current,
            self.runtime.web_port,
        )
        return True

    def _migrate_reserved_web_port(self) -> bool:
        """Move legacy or Integration API reserved editor ports to the current safe default.

        Versions through 0.3.4 used port 8099 or an Integration-API-plus-10000
        companion port. Ports 8000-9200 are reserved for Integration API services, so every
        persisted editor port in that range is migrated to 9201. The former
        automatically selected companion port is migrated as well. Explicit
        custom ports above 9200 are preserved.
        """

        current = self._config.settings.web_port
        requested = self.runtime.web_port
        migrate = current <= 9200

        if self.runtime.mode == "external" and os.environ.get("UC_CONFIG_HOME"):
            try:
                integration_port = int(os.environ.get("UC_INTEGRATION_HTTP_PORT", "9090"))
            except ValueError:
                integration_port = 9090
            old_companion = integration_port + 10000
            migrate = migrate or current == old_companion

        if not migrate or current == requested:
            return False

        self._config.settings = self._config.settings.model_copy(update={"web_port": requested})
        _LOG.info(
            "Migrated editor web port from %d to %d to avoid the Integration API reserved range",
            current,
            requested,
        )
        return True

    def _salvage_config(self, raw: Any) -> tuple[AppConfig, int]:
        """Keep every individually valid setting and automation from legacy/broken data."""
        default = self._default_config()
        if not isinstance(raw, dict):
            return default, 0

        settings = default.settings
        raw_settings = raw.get("settings")
        if isinstance(raw_settings, dict):
            accepted = settings.model_dump(mode="json")
            for field_name in Settings.model_fields:
                if field_name not in raw_settings:
                    continue
                candidate = dict(accepted)
                candidate[field_name] = raw_settings[field_name]
                try:
                    settings = Settings.model_validate(candidate)
                    accepted = settings.model_dump(mode="json")
                except (ValidationError, ValueError, TypeError):
                    _LOG.warning("Discarding invalid persisted setting: %s", field_name)

        source_automations = raw.get("automations", [])
        if not isinstance(source_automations, list):
            source_automations = []

        automations: list[Automation] = []
        seen_ids: set[str] = set()
        seen_commands: set[str] = set()
        skipped = 0
        for item in source_automations:
            if not isinstance(item, dict):
                skipped += 1
                continue
            migrated = dict(item)
            migrated.setdefault("command_enabled", True)
            migrated.setdefault("triggers", [])
            try:
                automation = Automation.model_validate(migrated)
            except (ValidationError, ValueError, TypeError):
                skipped += 1
                continue

            if automation.id in seen_ids:
                automation = automation.model_copy(update={"id": str(uuid4())})
            seen_ids.add(automation.id)

            if automation.command_enabled and automation.command in seen_commands:
                # Preserve the automation but disable only its duplicate command trigger.
                automation = automation.model_copy(update={"command_enabled": False})
            if automation.command_enabled:
                seen_commands.add(automation.command)
            automations.append(automation)

        return AppConfig(settings=settings, automations=automations), skipped

    def _backup_invalid_config(self) -> Path | None:
        if not self.path.exists():
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        suffix = ".dir" if self.path.is_dir() else ".json"
        backup = self.data_dir / f"config.invalid-{stamp}{suffix}"
        try:
            if self.path.is_dir():
                os.replace(self.path, backup)
            else:
                shutil.copy2(self.path, backup)
            return backup
        except OSError as copy_error:
            # A read-protected file may still be movable when its parent directory is writable.
            try:
                os.replace(self.path, backup)
                return backup
            except OSError as move_error:
                _LOG.error(
                    "Unable to back up invalid configuration %s: copy=%s move=%s",
                    self.path,
                    copy_error,
                    move_error,
                )
                return None

    @staticmethod
    def _best_effort_chmod(path: Path, mode: int) -> None:
        try:
            os.chmod(path, mode)
        except OSError as err:
            # FAT/CIFS/rootless bind mounts can reject chmod even though writes work.
            _LOG.warning("Unable to set permissions on %s: %s", path, err)

    def save(self) -> None:
        with self._lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_suffix(".tmp")
            payload = self._config.model_dump(mode="json")
            temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            self._best_effort_chmod(temp_path, 0o600)
            os.replace(temp_path, self.path)
            self._best_effort_chmod(self.path, 0o600)

    def snapshot(self) -> AppConfig:
        with self._lock:
            return self._config.model_copy(deep=True)

    def settings(self) -> Settings:
        return self.snapshot().settings

    def automations(self) -> list[Automation]:
        return self.snapshot().automations

    def get_automation(self, automation_id: str) -> Automation | None:
        return next((item for item in self.automations() if item.id == automation_id), None)

    def get_by_command(self, command: str) -> Automation | None:
        command = command.upper()
        return next((item for item in self.automations() if item.command == command), None)

    def update_settings(self, settings: Settings) -> Settings:
        with self._lock:
            self._config.settings = settings
            self.save()
            return settings.model_copy(deep=True)

    def replace_automations(self, automations: list[Automation]) -> list[Automation]:
        with self._lock:
            candidate = AppConfig(settings=self._config.settings, automations=automations)
            self._config = candidate
            self.save()
            return [item.model_copy(deep=True) for item in automations]

    def mutate(self, callback: Callable[[AppConfig], None]) -> AppConfig:
        with self._lock:
            candidate = self._config.model_copy(deep=True)
            callback(candidate)
            candidate = AppConfig.model_validate(candidate.model_dump(mode="json"))
            self._config = candidate
            self.save()
            return self.snapshot()
