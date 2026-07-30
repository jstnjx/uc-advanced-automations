"""Atomic JSON configuration persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Callable

from .models import AppConfig, Automation, Settings


class ConfigStore:
    """Read, validate and atomically persist application configuration."""

    def __init__(self, data_dir: Path | None = None) -> None:
        default_dir = Path(os.environ.get("UC_AUTOMATIONS_DATA_DIR", "~/.config/uc-advanced-automations"))
        self.data_dir = (data_dir or default_dir).expanduser().resolve()
        self.path = self.data_dir / "config.json"
        self._lock = RLock()
        self._config = AppConfig()
        self.load()

    def load(self) -> AppConfig:
        with self._lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            if not self.path.exists():
                self._config = AppConfig()
                self.save()
                return self.snapshot()

            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._config = AppConfig.model_validate(raw)
            return self.snapshot()

    def save(self) -> None:
        with self._lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_suffix(".tmp")
            payload = self._config.model_dump(mode="json")
            temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, self.path)
            os.chmod(self.path, 0o600)

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
