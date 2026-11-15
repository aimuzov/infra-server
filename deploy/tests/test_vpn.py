import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]


class SubscriptionStoreTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("subscription", ROOT / "vpn/store-subscription.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / "subscription.env"

    def save(self, value):
        self.module.store(io.StringIO(value), self.target)

    def test_stores_private_shell_quoted_url_without_touching_other_secrets(self):
        other = self.target.parent / ".env"
        other.write_text("OTHER=keep\n")
        url = "https://example.test/sub?value=$(touch%20no)&q='"
        self.save(json.dumps({"vpn_subscription_url": url}))
        result = subprocess.run(["bash", "-c", 'source "$1"; printf %s "$VPN_SUBSCRIPTION_URL"', "_", str(self.target)], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, url)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(other.read_text(), "OTHER=keep\n")


    def test_exact_workflow_producer_round_trips_through_store(self):
        workflow = (ROOT / ".github/workflows/deploy.yml").read_text()
        producer = next(
            line.strip().removesuffix("|").rstrip()
            for line in workflow.splitlines()
            if line.strip().startswith("python3 -c ")
        )
        url = "https://example.test/sub?value=$(touch%20no)&q='"
        result = subprocess.run(
            ["bash", "-o", "pipefail", "-c", producer],
            env=dict(os.environ, VPN_SUBSCRIPTION_URL=url),
            capture_output=True, text=True, check=True,
        )
        self.save(result.stdout)
        stored = subprocess.run(
            ["bash", "-c", 'source "$1"; printf %s "$VPN_SUBSCRIPTION_URL"', "_", str(self.target)],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(stored.stdout, url)

    def test_bad_payload_preserves_existing_secret(self):
        for payload in ['{}', '{"vpn_subscription_url":"http://example.test"}', '{"vpn_subscription_url":"https://a.test/\\nX=bad"}', 'not json']:
            with self.subTest(payload=payload):
                self.target.write_text("existing")
                with self.assertRaises(ValueError):
                    self.save(payload)
                self.assertEqual(self.target.read_text(), "existing")

    def test_empty_input_keeps_manual_deploy_compatible(self):
        self.target.write_text("existing")
        self.save("")
        self.assertEqual(self.target.read_text(), "existing")


class VpnUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vpn = self.root / "vpn"
        self.vpn.mkdir()
        shutil.copy(ROOT / "vpn/update.sh", self.vpn / "update.sh")
        self.config = self.vpn / "config.json"
        self.config.write_text('{"old":true}\n')
        (self.vpn / "subscription.env").write_text("VPN_SUBSCRIPTION_URL=https://example.test/sub\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.calls = self.root / "calls"
        self.fixture = self.root / "subscription.json"
        self.fixture.write_text(json.dumps([{"remarks": "EU Всё и сразу", "inbounds": [{"tag": "http"}, {"tag": "socks"}], "outbounds": [{"protocol": "vless"}]}]))
        self.script("flock", "exit 0")
        self.script("curl", 'if [[ ${DOWNLOAD_STATUS:-0} != 0 ]]; then echo secret-diagnostic >&2; exit 22; fi; while [[ $# -gt 0 ]]; do if [[ $1 == -o ]]; then cp "$FIXTURE" "$2"; exit; fi; shift; done')
        self.script("docker", 'echo "$*" >> "$CALLS"; if [[ $* == *"run -test"* ]]; then exit "${VALIDATE_STATUS:-0}"; fi; if [[ $* == *"ps --all"* ]]; then [[ ${PS_STATUS:-0} == 0 ]] || exit 1; echo vpn-container; fi; if [[ $* == *"up -d"* ]]; then exit "${RECREATE_STATUS:-0}"; fi')
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"], FIXTURE=str(self.fixture), CALLS=str(self.calls))

    def script(self, name, body):
        path = self.bin / name
        path.write_text("#!/usr/bin/env bash\nset -eu\n" + body + "\n")
        path.chmod(0o755)

    def run_update(self, **env):
        return subprocess.run(["bash", str(self.vpn / "update.sh")], env=dict(self.env, **env), capture_output=True, text=True)

    def test_invalid_xray_keeps_working_config(self):
        result = self.run_update(VALIDATE_STATUS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_text(), '{"old":true}\n')
        self.assertNotIn("up -d", self.calls.read_text())

    def test_missing_or_ambiguous_profile_keeps_working_config(self):
        for profiles in [[], [{"remarks": "Other"}], [{"remarks": "Всё и сразу"}] * 2]:
            self.fixture.write_text(json.dumps(profiles))
            result = self.run_update()
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.config.read_text(), '{"old":true}\n')

    def test_validated_config_is_applied_and_container_recreated(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(self.config.read_text())
        self.assertNotIn("remarks", config)
        self.assertEqual(config["inbounds"][0]["port"], 3128)
        self.assertIn("--force-recreate", self.calls.read_text())


    def test_failed_download_keeps_config_and_hides_sensitive_diagnostics(self):
        result = self.run_update(DOWNLOAD_STATUS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_text(), '{"old":true}\n')
        self.assertNotIn("secret-diagnostic", result.stderr)
        self.assertFalse(self.calls.exists())

    def test_unchanged_config_does_not_recreate_container(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.calls.write_text("")
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls.read_text(), "")

    def test_saved_subscription_overrides_legacy_env(self):
        (self.root / ".env").write_text("VPN_SUBSCRIPTION_URL=https://legacy.test/sub\nOTHER=keep\n")
        self.script("curl", '[[ ${!#} == https://example.test/sub ]] || exit 1; while [[ $# -gt 0 ]]; do if [[ $1 == -o ]]; then cp "$FIXTURE" "$2"; exit; fi; shift; done')
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OTHER=keep", (self.root / ".env").read_text())

    def test_existing_stopped_container_is_recreated(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls.read_text()
        self.assertIn("ps --all --quiet vpn", calls)
        self.assertIn("--force-recreate", calls)

    def test_container_lookup_failure_keeps_working_config(self):
        result = self.run_update(PS_STATUS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_text(), '{"old":true}\n')
        self.assertNotIn("up -d", self.calls.read_text())

    def test_failed_recreate_restores_previous_config(self):
        result = self.run_update(RECREATE_STATUS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_text(), '{"old":true}\n')


if __name__ == "__main__":
    unittest.main()
