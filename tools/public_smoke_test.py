#!/usr/bin/env python3
"""Public repository smoke checks that do not depend on the private test suite."""

from __future__ import annotations

import importlib
import json
import pkgutil
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PACKAGE = SRC / "uc_advanced_automations"
STATIC = PACKAGE / "static"

sys.path.insert(0, str(SRC))

from uc_advanced_automations import __version__  # noqa: E402
from uc_advanced_automations.models import Automation, Settings  # noqa: E402


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    root_driver = load_json(ROOT / "driver.json")
    package_driver = load_json(PACKAGE / "driver.json")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    versions = {
        "package": __version__,
        "pyproject": pyproject["project"]["version"],
        "driver.json": root_driver["version"],
        "packaged driver.json": package_driver["version"],
    }
    if len(set(versions.values())) != 1:
        raise AssertionError(f"Version metadata is inconsistent: {versions}")

    if root_driver != package_driver:
        raise AssertionError("Root and packaged driver.json files differ")

    # Import every Python module shipped by the package. This catches missing
    # runtime dependencies and import-time regressions without the private tests.
    imported_modules = []
    for module in pkgutil.walk_packages([str(PACKAGE)], prefix="uc_advanced_automations."):
        if module.name.endswith(".__main__"):
            continue
        importlib.import_module(module.name)
        imported_modules.append(module.name)
    if not imported_modules:
        raise AssertionError("No package modules were discovered for import validation")

    # Exercise model imports and basic schema construction.
    settings = Settings()
    automation = Automation(name="Public smoke test", command="SMOKE_TEST")
    assert settings.web_port >= 9201
    assert automation.command == "SMOKE_TEST"

    index = (STATIC / "index.html").read_text(encoding="utf-8")
    referenced_assets = set(
        re.findall(r'(?:src|href)="/static/([^"?]+)(?:\?[^" ]*)?"', index)
    )
    missing_assets = sorted(asset for asset in referenced_assets if not (STATIC / asset).is_file())
    if missing_assets:
        raise AssertionError(f"Missing frontend assets referenced by index.html: {missing_assets}")

    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    referenced_icons = set(re.findall(r'url\("/static/icons/([^"?]+)', css))
    missing_icons = sorted(icon for icon in referenced_icons if not (STATIC / "icons" / icon).is_file())
    if missing_icons:
        raise AssertionError(f"Missing Material Symbols referenced by styles.css: {missing_icons}")

    print(f"Public smoke checks passed for Advanced Automations v{__version__}")


if __name__ == "__main__":
    main()
