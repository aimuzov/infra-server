import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "vpn" / "update.sh"


class VpnUpdateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "vpn").mkdir()
        self.script = self.root / "vpn" / "update.sh"
        shutil.copy(SCRIPT, self.script)
        (self.root / ".env").write_text("VPN_SUBSCRIPTION_URL=https://subscription.invalid/s/secret-sentinel\n")
        self.fixture = self.root / "subscription.json"
        self.config = self.root / "vpn" / "config.json"
        self.old = {"inbounds": [{"tag": "http", "protocol": "http", "port": 3128}, {"tag": "socks", "protocol": "socks", "port": 1080}], "outbounds": [{"protocol": "vless", "tag": "old"}]}
        self.config.write_text(json.dumps(self.old))
        self.before = self.config.read_bytes()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "docker.log"
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"], SUB_FIXTURE=str(self.fixture), DOCKER_LOG=str(self.log))
        self.program("curl", "import os,sys,shutil\na=sys.argv[1:]\nshutil.copyfile(os.environ['SUB_FIXTURE'],a[a.index('-o')+1])\n")
        self.program("flock", "pass\n")
        self.program("docker", """import os,sys,json,pathlib
a=sys.argv[1:]
log=pathlib.Path(os.environ['DOCKER_LOG'])
with log.open('a') as f: f.write(json.dumps(a)+'\\n')
if 'ps' in a: print('vpn')
if 'run' in a:
 path=a[a.index('-v')+1].split(':/etc/')[0]
 try:
  c=json.loads(pathlib.Path(path).read_text())
  if c.get('reject'): sys.exit(1)
 except (ValueError,FileNotFoundError): sys.exit(1)
if 'restart' in a and os.environ.get('FAIL_RESTART')=='1':
 marker=log.with_suffix('.failed')
 if not marker.exists(): marker.touch();sys.exit(1)
""")

    def program(self, name, body):
        path = self.bin / name
        path.write_text("#!/usr/bin/env python3\n" + body)
        path.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def run_update(self, subscription, stale=False):
        self.fixture.write_text(json.dumps(subscription))
        result = subprocess.run(["bash", str(self.script), *(["--allow-stale"] if stale else [])], env=self.env, text=True, capture_output=True)
        self.assertNotIn("secret-sentinel", result.stdout + result.stderr)
        return result

    def profile(self, **extra):
        return {**self.old, "remarks": "Всё и сразу", "meta": {"secret": "private"}, **extra}

    def restarts(self):
        return sum("restart" in json.loads(line) for line in self.log.read_text().splitlines()) if self.log.exists() else 0

    def test_validates_and_installs_fresh_profile_before_restart(self):
        self.assertEqual(self.run_update([self.profile()]).returncode, 0)
        config = json.loads(self.config.read_text())
        self.assertNotIn("remarks", config)
        self.assertNotIn("meta", config)
        self.assertEqual(config["inbounds"][0]["listen"], "0.0.0.0")
        self.assertEqual(self.restarts(), 1)
        commands = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertLess(next(i for i,c in enumerate(commands) if "run" in c), next(i for i,c in enumerate(commands) if "restart" in c))

    def test_empty_subscription_is_an_error_for_the_timer(self):
        self.assertNotEqual(self.run_update([]).returncode, 0)
        self.assertEqual(self.config.read_bytes(), self.before)
        self.assertEqual(self.restarts(), 0)

    def test_deploy_reuses_only_a_valid_existing_config_and_warns(self):
        result = self.run_update([], stale=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("warning", (result.stdout + result.stderr).lower())
        self.assertEqual(self.config.read_bytes(), self.before)
        self.assertEqual(self.restarts(), 0)

    def test_deploy_fails_without_a_valid_cache(self):
        self.config.write_text("broken-json")
        self.assertNotEqual(self.run_update([], stale=True).returncode, 0)
        self.config.unlink()
        self.assertNotEqual(self.run_update([], stale=True).returncode, 0)

    def test_invalid_candidate_does_not_replace_the_cache(self):
        self.assertEqual(self.run_update([self.profile(reject=True)], stale=True).returncode, 0)
        self.assertEqual(self.config.read_bytes(), self.before)
        self.assertEqual(self.restarts(), 0)

    def test_invalid_or_ambiguous_profiles_preserve_the_cache(self):
        for value in ({}, None, "invalid", [{"remarks": "other"}], [self.profile(), self.profile()], [self.profile(inbounds=[])]):
            with self.subTest(value=value):
                self.assertNotEqual(self.run_update(value).returncode, 0)
                self.assertEqual(self.config.read_bytes(), self.before)
                self.assertEqual(self.restarts(), 0)

    def test_download_failure_can_only_reuse_a_valid_cache(self):
        self.env["FAIL_CURL"] = "1"
        self.assertNotEqual(self.run_update([]).returncode, 0)
        self.assertEqual(self.run_update([], stale=True).returncode, 0)
        self.assertEqual(self.config.read_bytes(), self.before)

    def test_failed_restart_restores_and_restarts_the_previous_config(self):
        self.env["FAIL_RESTART"] = "1"
        self.assertNotEqual(self.run_update([self.profile()]).returncode, 0)
        self.assertEqual(self.config.read_bytes(), self.before)
        self.assertEqual(self.restarts(), 2)
