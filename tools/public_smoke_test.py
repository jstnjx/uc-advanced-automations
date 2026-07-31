#!/usr/bin/env python3
"""Public repository smoke checks that do not depend on the private test suite."""

from __future__ import annotations

import importlib
import json
import pkgutil
import re
import struct
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


def png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise AssertionError(f"Not a readable PNG: {path}")
    return struct.unpack(">II", header[16:24])


def driver_icon_path(base: Path, descriptor: dict) -> Path:
    icon_reference = descriptor.get("icon")
    if not isinstance(icon_reference, str) or not icon_reference.startswith("custom:"):
        raise AssertionError("driver.json icon must use a custom:<filename> reference")
    return base / icon_reference.removeprefix("custom:")


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

    # The root descriptor is used for the direct Remote archive, while the
    # packaged descriptor is used by the installed Python package. Their
    # presentation/setup metadata may intentionally differ, but integration
    # identity and runtime-critical fields must remain aligned.
    critical_driver_fields = (
        "driver_id",
        "version",
        "min_core_api",
        "name",
        "icon",
        "port",
    )
    critical_mismatches = {
        field: {"root": root_driver.get(field), "packaged": package_driver.get(field)}
        for field in critical_driver_fields
        if root_driver.get(field) != package_driver.get(field)
    }
    if critical_mismatches:
        raise AssertionError(
            "Root and packaged driver.json files differ in runtime-critical fields: "
            + json.dumps(critical_mismatches, sort_keys=True, ensure_ascii=False)
        )

    descriptor_differences = sorted(
        field
        for field in set(root_driver) | set(package_driver)
        if root_driver.get(field) != package_driver.get(field)
    )
    if descriptor_differences:
        print(
            "Allowed root/packaged driver descriptor differences: "
            + ", ".join(descriptor_differences)
        )

    for label, icon_path in (
        ("root", driver_icon_path(ROOT, root_driver)),
        ("packaged", driver_icon_path(PACKAGE, package_driver)),
    ):
        if not icon_path.is_file():
            raise AssertionError(f"Missing {label} driver metadata icon: {icon_path}")
        dimensions = png_size(icon_path)
        if dimensions != (90, 90):
            raise AssertionError(
                f"{label.capitalize()} driver metadata icon must be 90x90, got "
                f"{dimensions[0]}x{dimensions[1]}: {icon_path}"
            )

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
