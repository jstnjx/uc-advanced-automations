"""Runtime target detection and target-specific defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

RuntimeMode = Literal["remote", "external"]


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class RuntimeEnvironment:
    """Resolved execution target for a shared embedded/external codebase."""

    mode: RuntimeMode
    data_dir: Path
    default_core_url: str
    web_host: str
    web_port: int

    @property
    def runs_on_remote(self) -> bool:
        return self.mode == "remote"

    @property
    def display_name(self) -> str:
        return "Remote Two/3" if self.runs_on_remote else "External service"


def detect_runtime() -> RuntimeEnvironment:
    """Detect Remote installation or external service operation.

    Explicit ``UC_RUNTIME_MODE`` wins. External deployments should set
    ``UC_EXTERNAL=true``. A Remote custom integration receives ``UC_CONFIG_HOME``;
    this is used as the embedded-mode signal when no explicit mode is supplied.
    """

    requested = os.environ.get("UC_RUNTIME_MODE", "").strip().lower()
    if requested not in {"", "remote", "external"}:
        raise ValueError("UC_RUNTIME_MODE must be 'remote' or 'external'")

    if requested:
        mode: RuntimeMode = requested  # type: ignore[assignment]
    elif _truthy(os.environ.get("UC_EXTERNAL")):
        mode = "external"
    elif os.environ.get("UC_CONFIG_HOME"):
        mode = "remote"
    else:
        mode = "external"

    explicit_data_dir = os.environ.get("UC_AUTOMATIONS_DATA_DIR")
    if explicit_data_dir:
        data_dir = Path(explicit_data_dir)
    elif mode == "remote" and os.environ.get("UC_CONFIG_HOME"):
        data_dir = Path(os.environ["UC_CONFIG_HOME"])
    else:
        data_dir = Path("~/.config/uc-advanced-automations")

    core_url = os.environ.get(
        "UC_CORE_URL",
        "ws://127.0.0.1/ws" if mode == "remote" else "ws://remote.local/ws",
    )
    web_host = os.environ.get("UC_AUTOMATIONS_WEB_HOST", "0.0.0.0")
    try:
        web_port = int(os.environ.get("UC_AUTOMATIONS_WEB_PORT", "8099"))
    except ValueError as err:
        raise ValueError("UC_AUTOMATIONS_WEB_PORT must be an integer") from err

    return RuntimeEnvironment(
        mode=mode,
        data_dir=data_dir.expanduser().resolve(),
        default_core_url=core_url,
        web_host=web_host,
        web_port=web_port,
    )
