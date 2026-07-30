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
        self.assertEqual(source.count("id: createId(),"), 2)
        self.assertNotIn("id: crypto.randomUUID(),", source)
        self.assertIn("cryptoApi.getRandomValues(bytes)", source)
        self.assertIn("Math.floor(Math.random() * 256)", source)

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
