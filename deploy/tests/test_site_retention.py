import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "site_retention", Path(__file__).parents[1] / "site-retention.py"
)
retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)


REPO = "ghcr.io/aimuzov/example"


def digest(version):
    return "sha256:" + version * 64


class FakeDocker:
    def __init__(self):
        self.references = {}
        self.running = []
        self.removed = []
        self.fail_removal = False

    def add(self, reference, version):
        self.references[reference] = {
            "Id": digest(version),
            "Descriptor": {"digest": digest(version)},
        }
        return reference

    def current(self, version, services=("app",)):
        reference = self.add(REPO + "@" + digest(version), version)
        self.running = [
            {
                "Name": "/example-" + service,
                "Image": digest(version),
                "ImageManifestDescriptor": {"digest": digest(version)},
                "Config": {
                    "Image": reference,
                    "Labels": {
                        "com.docker.compose.project": "example",
                        "com.docker.compose.service": service,
                    },
                },
                "State": {"Running": True},
            }
            for service in services
        ]

    def containers(self):
        return self.running

    def image(self, reference):
        return self.references.get(reference)

    def image_references(self):
        return list(self.references)

    def remove(self, reference):
        if self.fail_removal:
            raise RuntimeError("Docker refused removal")
        self.removed.append(reference)
        del self.references[reference]


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.docker = FakeDocker()
        self.policy = retention.Retention(self.root, self.docker)
        self.git = patch.object(retention, "revision_exists", return_value=True)
        self.git.start()
        self.addCleanup(self.git.stop)

    def state(self, version, suffix, services=("app",)):
        directory = self.root / "example" / ("rollback." + suffix)
        directory.mkdir(parents=True)
        (directory / "revision").write_text("a" * 40 + "\n")
        (directory / "compose").write_text("deploy/docker-compose.yml\n")
        lines = ["services:"]
        for service in services:
            reference = self.docker.add(
                "site-rollback/example-" + service + ":" + version * 64, version
            )
            lines.extend(["  " + service + ":", "    image: " + reference])
        (directory / "images.yml").write_text("\n".join(lines) + "\n")
        return directory

    def register(self, state=None, services=("app",)):
        self.policy.register(
            "example", ["example-" + service for service in services], state
        )

    def test_rotates_three_versions_and_keeps_one_previous_state(self):
        self.docker.current("b")
        previous = self.state("a", "previous")
        obsolete = self.state("0", "obsolete")
        old = self.docker.add(REPO + "@" + digest("0"), "0")
        self.register(previous)
        self.policy.cleanup()
        self.assertIn(old, self.docker.removed)
        self.assertTrue(previous.exists())
        self.assertFalse(obsolete.exists())
        self.assertEqual(self.policy.config("example")["previous"], previous.name)

        self.docker.current("c")
        second_previous = self.state("b", "second")
        self.register(second_previous)
        self.policy.cleanup()
        self.assertFalse(previous.exists())
        self.assertTrue(second_previous.exists())
        self.assertNotIn(REPO + "@" + digest("b"), self.docker.removed)
        self.assertNotIn(REPO + "@" + digest("c"), self.docker.removed)

    def test_redeploy_preserves_previous_distinct_version(self):
        self.docker.current("b")
        previous = self.state("a", "previous")
        self.register(previous)
        repeated = self.state("b", "repeated")
        self.register(repeated)
        self.policy.cleanup()
        self.assertEqual(self.policy.config("example")["previous"], previous.name)
        self.assertTrue(previous.exists())
        self.assertFalse(repeated.exists())

    def test_failed_deployment_does_not_replace_successful_rollback(self):
        self.docker.current("b")
        previous = self.state("a", "previous")
        self.register(previous)
        failed = self.state("b", "failed")
        bad_image = self.docker.add(REPO + "@" + digest("c"), "c")
        self.policy.cleanup()
        self.assertEqual(self.policy.config("example")["previous"], previous.name)
        self.assertIn(bad_image, self.docker.removed)
        self.assertFalse(failed.exists())

    def test_protects_images_referenced_by_stopped_containers(self):
        self.docker.current("b")
        used = self.docker.add(REPO + ":keep-stopped", "0")
        self.docker.running.append(
            {
                "Name": "/unrelated-stopped",
                "Image": digest("0"),
                "ImageManifestDescriptor": {"digest": digest("0")},
                "Config": {"Image": used, "Labels": {}},
                "State": {"Running": False},
            }
        )
        self.register()
        self.policy.cleanup()
        self.assertNotIn(used, self.docker.removed)

    def test_protects_shared_multi_service_rollback_image(self):
        services = ("site", "bot")
        self.docker.current("b", services)
        previous = self.state("a", "previous", services)
        extra = self.docker.add(REPO + ":same-native-manifest", "a")
        self.register(previous, services)
        self.policy.cleanup()
        self.assertNotIn(extra, self.docker.removed)
        self.assertEqual(len(self.docker.removed), 0)

    def test_adoption_skips_stale_states_with_missing_images(self):
        self.docker.current("b")
        previous = self.state("a", "previous")
        stale = self.state("0", "stale")
        del self.docker.references["site-rollback/example-app:" + "0" * 64]
        self.register()
        self.assertEqual(self.policy.config("example")["previous"], previous.name)
        self.policy.cleanup()
        self.assertTrue(previous.exists())
        self.assertFalse(stale.exists())

    def test_corrupt_previous_state_stops_cleanup_before_deletion(self):
        self.docker.current("b")
        previous = self.state("a", "previous")
        self.register(previous)
        (previous / "images.yml").write_text("invalid")
        old = self.docker.add(REPO + ":obsolete", "0")
        with self.assertRaises(ValueError):
            self.policy.cleanup()
        self.assertEqual(self.docker.removed, [])
        self.assertIn(old, self.docker.references)

    def test_invalid_new_rollback_does_not_replace_existing_config(self):
        self.docker.current("b")
        previous = self.state("a", "previous")
        self.register(previous)
        invalid = self.state("0", "invalid")
        (invalid / "revision").write_text("invalid")
        with self.assertRaises(ValueError):
            self.register(invalid)
        self.assertEqual(self.policy.config("example")["previous"], previous.name)

    def test_dry_run_and_unmanaged_repositories(self):
        self.docker.current("b")
        self.register()
        state = self.state("0", "obsolete")
        old = self.docker.add(REPO + ":obsolete", "0")
        unrelated = self.docker.add("reabica-app-api:before-transfer", "1")
        self.policy.cleanup(dry_run=True)
        self.assertEqual(self.docker.removed, [])
        self.assertTrue(state.exists())
        self.policy.cleanup()
        self.assertIn(old, self.docker.removed)
        self.assertNotIn(unrelated, self.docker.removed)

    def test_docker_failure_keeps_rollback_metadata(self):
        self.docker.current("b")
        previous = self.state("a", "previous")
        obsolete = self.state("0", "obsolete")
        self.register(previous)
        self.docker.fail_removal = True
        with self.assertRaises(RuntimeError):
            self.policy.cleanup()
        self.assertTrue(previous.exists())
        self.assertTrue(obsolete.exists())

    def test_invalid_state_path_and_wrong_git_revision_are_rejected(self):
        self.docker.current("b")
        outside = self.root / "outside"
        outside.mkdir()
        with self.assertRaises(ValueError):
            self.register(outside)
        previous = self.state("a", "previous")
        with patch.object(retention, "revision_exists", return_value=False):
            with self.assertRaises(ValueError):
                self.register(previous)

    def test_removed_project_is_preserved_without_breaking_scheduled_cleanup(self):
        self.docker.current("b")
        previous = self.state("a", "previous")
        self.register(previous)
        old = self.docker.add(REPO + ":obsolete", "0")
        self.docker.running = []
        self.policy.cleanup()
        self.assertEqual(self.docker.removed, [])
        self.assertTrue(previous.exists())
        self.assertIn(old, self.docker.references)

    def test_registry_change_keeps_ownership_of_old_repository(self):
        self.docker.current("b")
        self.register()
        self.docker.current("c")
        self.docker.running[0]["Config"]["Image"] = "ghcr.io/lazyvimx/new-name@" + digest("c")
        previous = self.state("b", "previous")
        self.register(previous)
        self.assertEqual(
            self.policy.config("example")["repositories"],
            [REPO, "ghcr.io/lazyvimx/new-name"],
        )


if __name__ == "__main__":
    unittest.main()
