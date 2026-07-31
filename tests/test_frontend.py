from pathlib import Path
import re
import shutil
import subprocess
import unittest


APP_JS = Path("src/uc_advanced_automations/static/app.js")


class FrontendCompatibilityTests(unittest.TestCase):
    def test_uuid_helper_replaces_direct_random_uuid_calls(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")
        self.assertIn("function createId()", source)
        self.assertGreaterEqual(source.count("createId()"), 4)
        self.assertNotIn("id: crypto.randomUUID(),", source)
        self.assertIn("cryptoApi.getRandomValues(bytes)", source)
        self.assertIn("Math.floor(Math.random() * 256)", source)

    def test_streamlined_builder_and_modal_contract(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")
        html = Path("src/uc_advanced_automations/static/index.html").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"\b(alert|confirm|prompt)\s*\(")
        self.assertIn('id="messageDialog"', html)
        self.assertIn('id="stepDialog"', html)
        self.assertIn('option value="replace"', html)
        self.assertIn('id="triggerMode"', html)
        self.assertIn("attachSortable", source)
        self.assertIn("attributeField", source)
        self.assertIn("Sensors are read-only", source)
        self.assertNotIn("Running on:", source)
        self.assertNotIn("UNFOLDED CIRCLE", html)
        self.assertNotRegex(html, r"\bUC\b")
        self.assertNotRegex(source, r"\bUC\b")
        for step in range(4):
            self.assertIn(f'data-flow-step="{step}"', html)
            self.assertIn(f'data-flow-panel="{step}"', html)
        self.assertIn('id="rawEditorDialog"', html)
        self.assertIn('id="blueprintDialog"', html)
        self.assertIn('id="savingOverlay"', html)
        self.assertIn("buildBlueprint", source)
        self.assertIn("createFromBlueprint", source)
        self.assertIn("setSaving(true)", source)
        self.assertIn("Require every condition to match", source)
        self.assertNotIn("AND — all conditions", source)

    def test_overview_entity_filters_and_manual_logs_contract(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")
        html = Path("src/uc_advanced_automations/static/index.html").read_text(encoding="utf-8")
        self.assertIn('id="automationOverview"', html)
        self.assertIn('id="editAutomation"', html)
        self.assertIn('id="automationTimeline"', html)
        self.assertIn('id="entityDropdownToggle"', html)
        self.assertIn('class="entity-filter-details"', html)
        self.assertIn('id="entityTypeFilters"', html)
        self.assertIn('id="entityIntegrationFilters"', html)
        self.assertIn('id="refreshLogs"', html)
        self.assertIn('id="continuousLogs"', html)
        self.assertIn("function renderOverview", source)
        self.assertIn("function entityIntegration", source)
        self.assertIn("function setContinuousLogPolling", source)
        self.assertNotIn("setInterval(pollLogs", source.split("async function init()", 1)[1])
        self.assertIn('window.location.reload();', source)
        self.assertIn('Deleting automation…', source)
        self.assertNotIn('A–Z, numbers and underscores', html)
        self.assertIn('`${labelText} - ${currentText}`', source)
        self.assertNotIn("A read-only timeline of how this automation starts and what it does.", html)
        self.assertNotIn("Entity references are included as mapping slots", html)
        self.assertNotIn('class="selection-summary"', html)
        self.assertNotIn('class="entity-selection-grid"', html)

    def test_wait_timeframe_after_trigger_contract(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")
        self.assertIn('value: "trigger_timeframe"', source)
        self.assertIn('Timeframe after trigger', source)
        self.assertIn('remaining sequence is skipped', source)

    def test_diamond_assets_are_used_for_icon_and_favicon(self) -> None:
        html = Path("src/uc_advanced_automations/static/index.html").read_text(encoding="utf-8")
        driver = Path("driver.json").read_text(encoding="utf-8")
        self.assertIn("/static/favicon.png", html)
        self.assertIn("/static/integration-icon.png", html)
        self.assertIn('"icon": "custom:advanced-automations.png"', driver)
        self.assertTrue(Path("advanced-automations.png").is_file())
        self.assertTrue(Path("src/uc_advanced_automations/static/favicon.png").is_file())

    def test_local_material_symbols_weight_200_svg_set(self) -> None:
        html = Path("src/uc_advanced_automations/static/index.html").read_text(encoding="utf-8")
        css = Path("src/uc_advanced_automations/static/styles.css").read_text(encoding="utf-8")
        source = APP_JS.read_text(encoding="utf-8")
        icon_dir = Path("src/uc_advanced_automations/static/icons")
        required = {
            "add.svg", "arrow_back.svg", "arrow_forward.svg", "close.svg", "code.svg",
            "delete.svg", "devices.svg", "drag_indicator.svg", "edit.svg", "expand_more.svg",
            "filter_list.svg", "play_arrow.svg", "refresh.svg", "save.svg", "search.svg",
            "settings.svg", "share.svg", "timer.svg", "upload_file.svg",
        }
        self.assertTrue(required.issubset({path.name for path in icon_dir.glob("*.svg")}))
        for name in required:
            svg = (icon_dir / name).read_text(encoding="utf-8")
            self.assertIn('viewBox="0 -960 960 960"', svg)
            self.assertIn("<path", svg)
        self.assertFalse(Path("src/uc_advanced_automations/static/material-symbols-outlined.woff2").exists())
        self.assertNotIn("@font-face", css)
        self.assertIn('--mi-url: url("/static/icons/add.svg?v=0.6.2")', css)
        self.assertIn('class="mi mi-refresh"', html)
        self.assertIn('class="mi mi-settings"', html)
        self.assertIn('class="mi mi-filter_list"', html)
        self.assertIn('function materialIcon', source)
        self.assertIn('materialIcon("arrow_forward", "automation-chevron")', source)
        self.assertIn('materialIcon("drag_indicator", "drag-handle")', source)
        combined = html + css + source
        self.assertNotIn("material-symbols-outlined", combined)
        self.assertNotIn("fonts.googleapis.com", combined)
        self.assertNotIn("fonts.gstatic.com", combined)
        self.assertNotRegex(html, r'\stitle="')
        self.assertNotRegex(source, r"\.title\s*=")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the frontend compatibility test")
    def test_uuid_helper_runs_without_web_crypto(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")
        match = re.search(r"function createId\(\) \{.*?\n\}", source, re.DOTALL)
        self.assertIsNotNone(match)
        script = (
            'const vm = require("node:vm");\n'
            f'const source = {match.group(0)!r} + "\\ncreateId();";\n'
            'const value = vm.runInNewContext(source, { Uint8Array, Math, Array });\n'
            'if (!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value)) {\n'
            '  throw new Error(`Invalid UUID fallback: ${value}`);\n'
            '}\n'
        )
        subprocess.run(["node", "-e", script], check=True)


if __name__ == "__main__":
    unittest.main()
