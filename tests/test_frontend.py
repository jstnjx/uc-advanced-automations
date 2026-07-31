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

    def test_diamond_assets_are_used_for_icon_and_favicon(self) -> None:
        html = Path("src/uc_advanced_automations/static/index.html").read_text(encoding="utf-8")
        driver = Path("driver.json").read_text(encoding="utf-8")
        self.assertIn("/static/favicon.png", html)
        self.assertIn("/static/integration-icon.png", html)
        self.assertIn('"icon": "custom:advanced-automations.png"', driver)
        self.assertTrue(Path("advanced-automations.png").is_file())
        self.assertTrue(Path("src/uc_advanced_automations/static/favicon.png").is_file())

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
