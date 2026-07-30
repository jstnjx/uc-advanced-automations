from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from uc_advanced_automations.startup import initialize_integration_api, start_web_site
from uc_advanced_automations.config_store import ConfigStore
from uc_advanced_automations.integration import IntegrationController
from uc_advanced_automations.runtime import detect_runtime
from uc_advanced_automations.web import create_app, get_health
from aiohttp import web


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_integration_api_failure_is_reported_without_escaping(self):
        class BrokenApi:
            async def init(self, *_args):
                raise RuntimeError("zeroconf unavailable")

        status = {}
        result = await initialize_integration_api(BrokenApi(), "driver.json", object(), status)
        self.assertFalse(result)
        self.assertFalse(status["integration_api_ready"])
        self.assertIn("zeroconf unavailable", status["integration_api_error"])

    async def test_health_endpoint_is_live_during_api_initialization(self):
        class Core:
            is_connected = False

        class Runtime:
            mode = "external"

        class Request:
            app = {
                "service_status": {
                    "integration_api_ready": False,
                    "integration_api_error": "still starting",
                },
                "core": Core(),
                "runtime": Runtime(),
            }

        response = await get_health(Request())
        self.assertEqual(response.status, 200)
        payload = json.loads(response.text)
        self.assertEqual(payload["status"], "ok")
        self.assertFalse(payload["integration_api_ready"])

    async def test_web_site_avoids_integration_api_port(self):
        app = web.Application()
        runner = web.AppRunner(app)
        await runner.setup()
        status = {}
        with tempfile.TemporaryDirectory() as directory:
            port_file = Path(directory) / "web-port"
            _site, actual_port = await start_web_site(
                runner,
                "127.0.0.1",
                39091,
                39091,
                status,
                port_file,
            )
            self.assertNotEqual(actual_port, 39091)
            self.assertEqual(int(port_file.read_text().strip()), actual_port)
            self.assertTrue(status["web_port_fallback"])
        await runner.cleanup()


class ConfigurationRecoveryTests(unittest.TestCase):
    def test_invalid_json_is_backed_up_and_recovered(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ",
            {"UC_RUNTIME_MODE": "external", "UC_AUTOMATIONS_DATA_DIR": directory},
            clear=True,
        ):
            path = Path(directory) / "config.json"
            path.write_text("{invalid-json\n", encoding="utf-8")
            store = ConfigStore()
            status = store.recovery_status
            self.assertTrue(status["config_recovered"])
            self.assertTrue(Path(status["config_backup"]).is_file())
            self.assertEqual(store.automations(), [])
            json.loads(path.read_text(encoding="utf-8"))

    def test_valid_legacy_automation_survives_partial_recovery(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ",
            {"UC_RUNTIME_MODE": "external", "UC_AUTOMATIONS_DATA_DIR": directory},
            clear=True,
        ):
            raw = {
                "settings": {"core_url": "ws://remote.local/ws", "web_port": "invalid"},
                "automations": [
                    {"name": "Legacy", "command": "LEGACY", "steps": []},
                    {"name": "Broken", "command": "!", "steps": []},
                ],
            }
            (Path(directory) / "config.json").write_text(json.dumps(raw), encoding="utf-8")
            store = ConfigStore()
            self.assertTrue(store.recovery_status["config_recovered"])
            self.assertEqual([item.command for item in store.automations()], ["LEGACY"])
            self.assertTrue(store.automations()[0].command_enabled)

    def test_command_handler_exposes_ucapi_websocket_parameter(self):
        import inspect
        signature = inspect.signature(IntegrationController.command_handler)
        self.assertIn("websocket", signature.parameters)
        self.assertEqual(signature.parameters["websocket"].kind, inspect.Parameter.KEYWORD_ONLY)


class RuntimeEnvironmentTests(unittest.TestCase):
    def test_external_mode_applies_container_safe_ucapi_defaults(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ",
            {
                "UC_RUNTIME_MODE": "external",
                "UC_AUTOMATIONS_DATA_DIR": directory,
            },
            clear=True,
        ):
            runtime = detect_runtime()
            runtime.apply_process_environment(19090)
            import os

            self.assertEqual(os.environ["UC_DISABLE_MDNS_PUBLISH"], "true")
            self.assertEqual(os.environ["UC_INTEGRATION_INTERFACE"], "0.0.0.0")
            self.assertEqual(os.environ["UC_INTEGRATION_HTTP_PORT"], "19090")

    def test_explicit_mdns_setting_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ",
            {
                "UC_RUNTIME_MODE": "external",
                "UC_AUTOMATIONS_DATA_DIR": directory,
                "UC_DISABLE_MDNS_PUBLISH": "false",
            },
            clear=True,
        ):
            runtime = detect_runtime()
            runtime.apply_process_environment()
            import os

            self.assertEqual(os.environ["UC_DISABLE_MDNS_PUBLISH"], "false")


class PackagingContractTests(unittest.TestCase):

    def test_installer_entrypoint_uses_config_mount_and_companion_web_port(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {
                "PATH": os.environ.get("PATH", ""),
                "UC_RUNTIME_MODE": "external",
                "UC_EXTERNAL": "true",
                "UC_CONFIG_HOME": directory,
                "UC_INTEGRATION_HTTP_PORT": "8123",
                "UC_DISABLE_MDNS_PUBLISH": "true",
            }
            result = subprocess.run(
                [
                    "sh",
                    "tools/docker-entrypoint.sh",
                    "sh",
                    "-c",
                    'printf "%s|%s" "$UC_AUTOMATIONS_DATA_DIR" "$UC_AUTOMATIONS_WEB_PORT"',
                ],
                cwd=Path.cwd(),
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.stdout, f"{directory}|18123")

    def test_remote_workflow_does_not_execute_scripts_directly(self):
        workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")
        self.assertIn("bash ./tools/build_remote.sh aarch64", workflow)
        self.assertIn('bash ./tools/verify_remote_archive.sh "$ARCHIVE"', workflow)
        self.assertIn("Build and smoke-test external container with installer contract", workflow)
        self.assertIn("/api/health", workflow)
        self.assertIn("UC_CONFIG_HOME=/config", workflow)
        self.assertIn("--network host", workflow)
        self.assertIn("config.invalid-*.json", workflow)
        self.assertIn("container-image:", workflow)
        self.assertIn("packages: write", workflow)
        self.assertIn("docker/login-action@v4", workflow)
        self.assertIn("docker/metadata-action@v6", workflow)
        self.assertIn("docker/build-push-action@v7", workflow)
        self.assertIn("platforms: linux/amd64,linux/arm64", workflow)
        self.assertIn("type=raw,value=latest", workflow)
        self.assertIn("needs: [remote-package, external-package, container-image]", workflow)

    def test_external_image_has_stable_runtime_contract(self):
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        self.assertIn("UC_RUNTIME_MODE=external", dockerfile)
        self.assertIn("UC_DISABLE_MDNS_PUBLISH=true", dockerfile)
        self.assertIn("tools/healthcheck.py", dockerfile)
        self.assertIn('["/bin/sh", "/app/tools/docker-entrypoint.sh"]', dockerfile)
        self.assertIn('CMD ["uc-advanced-automations"]', dockerfile)
        entrypoint = Path("tools/docker-entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn('exec "$@"', entrypoint)
        self.assertIn('UC_CONFIG_HOME', entrypoint)
        self.assertIn('$((UC_INTEGRATION_HTTP_PORT + 10000))', entrypoint)


if __name__ == "__main__":
    unittest.main()
