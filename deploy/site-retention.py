#!/usr/bin/env python3
"""Retain the active GHCR images and one usable rollback per registered project."""

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path("/var/lib/site-deploy")
LOCK = Path("/tmp/sites-deploy.lock")
NAME = re.compile(r"[a-z0-9][a-z0-9_.-]*")
SHA = re.compile(r"sha256:[0-9a-f]{64}")
STATE = re.compile(r"rollback\.[A-Za-z0-9]+")
REGISTRY_IMAGE = re.compile(
    r"(ghcr\.io/(?:aimuzov|lazyvimx)/[a-z0-9_.-]+)@(sha256:[0-9a-f]{64})"
)


def revision_exists(project, revision, compose):
    for target in (revision + "^{commit}", revision + ":" + compose):
        result = subprocess.run(
            ["git", "-C", "/srv/" + project, "cat-file", "-e", target],
            capture_output=True,
        )
        if result.returncode:
            return False
    return True


class Docker:
    def containers(self):
        identifiers = subprocess.check_output(
            ["docker", "ps", "-aq"], text=True
        ).split()
        if not identifiers:
            return []
        return json.loads(
            subprocess.check_output(["docker", "inspect", *identifiers], text=True)
        )

    def image(self, reference):
        result = subprocess.run(
            [
                "docker", "image", "inspect", "--platform", "linux/amd64",
                "--format", "{{json .}}", reference,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            if "No such image" in result.stderr:
                return None
            raise RuntimeError(result.stderr.strip())
        return json.loads(result.stdout)

    def image_references(self):
        output = subprocess.check_output(
            ["docker", "image", "ls", "--digests", "--format", "{{json .}}"],
            text=True,
        )
        references = set()
        for line in output.splitlines():
            row = json.loads(line)
            repository = row["Repository"]
            if repository == "<none>":
                continue
            if SHA.fullmatch(row["Digest"]):
                references.add(repository + "@" + row["Digest"])
            if row["Tag"] != "<none>":
                references.add(repository + ":" + row["Tag"])
        return sorted(references)

    def remove(self, reference):
        subprocess.run(["docker", "image", "rm", reference], check=True)


def manifest(image):
    # Docker's platform descriptor has the same digest as the container's
    # ImageManifestDescriptor, including images with a provenance index.
    descriptor = image.get("Descriptor", {})
    value = descriptor.get("digest", descriptor.get("Digest", ""))
    if not SHA.fullmatch(value):
        raise ValueError("Missing native image manifest")
    return value


class Retention:
    def __init__(self, root, docker):
        self.root = root
        self.docker = docker

    def directory(self, project):
        if not NAME.fullmatch(project):
            raise ValueError("Invalid project name")
        path = self.root / project
        if path.is_symlink():
            raise ValueError("Project state directory is a symlink")
        return path

    def config(self, project):
        path = self.directory(project) / "retention.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        if (
            data.get("version") != 1
            or not isinstance(data.get("containers"), list)
            or not data["containers"]
            or not all(isinstance(n, str) and NAME.fullmatch(n) for n in data["containers"])
            or not isinstance(data.get("repositories"), list)
            or not data["repositories"]
            or not all(
                isinstance(r, str) and REGISTRY_IMAGE.fullmatch(r + "@" + "sha256:" + "0" * 64)
                for r in data["repositories"]
            )
            or (data.get("previous") is not None and not STATE.fullmatch(data["previous"]))
        ):
            raise ValueError("Invalid retention configuration for " + project)
        return data

    def write_config(self, project, config):
        directory = self.directory(project)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as file:
            temporary = Path(file.name)
            try:
                json.dump(config, file, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            except BaseException:
                temporary.unlink()
                raise
        try:
            os.replace(temporary, directory / "retention.json")
        finally:
            temporary.unlink(missing_ok=True)

    def current(self, project, names):
        containers = {c["Name"].lstrip("/"): c for c in self.docker.containers()}
        selected = {}
        for name in names:
            container = containers.get(name)
            if container is None:
                raise ValueError("Missing container " + name)
            labels = container["Config"].get("Labels") or {}
            service = labels.get("com.docker.compose.service")
            if (
                labels.get("com.docker.compose.project") != project
                or not service
                or not NAME.fullmatch(service)
                or service in selected
            ):
                raise ValueError("Unexpected Compose identity for " + name)
            if not SHA.fullmatch(container["ImageManifestDescriptor"]["digest"]):
                raise ValueError("Missing container image manifest")
            selected[service] = container
        return selected

    def state(self, project, path, current):
        directory = self.directory(project)
        if path.parent != directory or not STATE.fullmatch(path.name) or path.is_symlink():
            return None
        try:
            revision = (path / "revision").read_text().strip()
            compose = (path / "compose").read_text().strip()
            lines = (path / "images.yml").read_text().splitlines()
        except FileNotFoundError:
            return None
        if (
            not re.fullmatch(r"[0-9a-f]{40}", revision)
            or not re.fullmatch(r"deploy/[a-zA-Z0-9_.-]+\.yml", compose)
            or not revision_exists(project, revision, compose)
            or not lines
            or lines[0] != "services:"
            or len(lines) != 1 + 2 * len(current)
        ):
            return None
        images = {}
        for index in range(1, len(lines), 2):
            service_line = re.fullmatch(r"  ([a-z0-9_.-]+):", lines[index])
            image_line = re.fullmatch(r"    image: (\S+)", lines[index + 1])
            if not service_line or not image_line:
                return None
            service, reference = service_line[1], image_line[1]
            if service not in current or service in images:
                return None
            name = current[service]["Name"].lstrip("/")
            prefix = "site-rollback/" + name + ":"
            if not reference.startswith(prefix):
                return None
            expected = "sha256:" + reference[len(prefix):]
            if not SHA.fullmatch(expected):
                return None
            image = self.docker.image(reference)
            if image is None or manifest(image) != expected:
                return None
            images[service] = (reference, expected)
        return images

    @staticmethod
    def different(images, current):
        return any(
            value[1] != current[service]["ImageManifestDescriptor"]["digest"]
            for service, value in images.items()
        )

    def register(self, project, names, state=None):
        current = self.current(project, names)
        repositories = set()
        for container in current.values():
            image = REGISTRY_IMAGE.fullmatch(container["Config"]["Image"])
            if image is None or not container["State"]["Running"]:
                raise ValueError("Expected a running GHCR container")
            repositories.add(image[1])
        old_config = self.config(project)
        if old_config:
            repositories.update(old_config["repositories"])
        previous = None
        if state is not None:
            images = self.state(project, state, current)
            if images is None:
                raise ValueError("New rollback state is incomplete")
            if self.different(images, current):
                previous = state.name
        if previous is None and old_config and old_config["previous"]:
            path = self.directory(project) / old_config["previous"]
            images = self.state(project, path, current)
            if images is None:
                raise ValueError("Saved rollback state is unavailable")
            if self.different(images, current):
                previous = path.name
        if previous is None:
            candidates = sorted(
                self.directory(project).glob("rollback.*"),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
            for path in candidates:
                images = self.state(project, path, current)
                if images and self.different(images, current):
                    previous = path.name
                    break
        self.write_config(project, {
            "version": 1,
            "containers": sorted(names),
            "repositories": sorted(repositories),
            "previous": previous,
        })

    def cleanup(self, projects=None, dry_run=False):
        configs = {}
        protected = set()
        protected_repositories = set()
        for container in self.docker.containers():
            protected.add(container["Image"])
            protected.add(container["Config"]["Image"])
            protected.add(container.get("ImageManifestDescriptor", {}).get("digest", ""))
        for path in sorted(self.root.glob("*/retention.json")):
            project = path.parent.name
            config = self.config(project)
            try:
                current = self.current(project, config["containers"])
            except ValueError as error:
                protected_repositories.update(config["repositories"])
                protected_repositories.update(
                    "site-rollback/" + name for name in config["containers"]
                )
                print("Keeping unavailable project " + project + ": " + str(error), flush=True)
                continue
            if config["previous"]:
                previous = self.state(
                    project, self.directory(project) / config["previous"], current
                )
                if previous is None:
                    raise ValueError("Saved rollback state is unavailable for " + project)
                for reference, native_manifest in previous.values():
                    protected.update((reference, native_manifest))
            configs[project] = config
        selected = set(configs) if projects is None else set(projects)
        if not selected.issubset(configs):
            raise ValueError("Project is not registered for image retention")
        repositories = set()
        for project in selected:
            config = configs[project]
            repositories.update(config["repositories"])
            repositories.update("site-rollback/" + name for name in config["containers"])
        for reference in self.docker.image_references():
            repository = reference.split("@")[0].rsplit(":", 1)[0]
            if (
                repository not in repositories
                or repository in protected_repositories
                or reference in protected
            ):
                continue
            image = self.docker.image(reference)
            if image is None:
                continue
            if image["Id"] in protected or manifest(image) in protected:
                continue
            print(("Would remove image " if dry_run else "Removing image ") + reference, flush=True)
            if not dry_run:
                self.docker.remove(reference)
        for project in selected:
            config = configs[project]
            for path in sorted(self.directory(project).glob("rollback.*")):
                if (
                    path.name == config["previous"]
                    or not STATE.fullmatch(path.name)
                    or path.is_symlink()
                    or not path.is_dir()
                ):
                    continue
                print(("Would remove state " if dry_run else "Removing state ") + str(path), flush=True)
                if not dry_run:
                    shutil.rmtree(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock-fd", type=int, help="Reuse the site-build deployment lock")
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record")
    record.add_argument("project")
    record.add_argument("state", type=Path)
    record.add_argument("containers", nargs="+")
    adopt = commands.add_parser("adopt")
    adopt.add_argument("projects", nargs="+")
    clean = commands.add_parser("cleanup")
    clean.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    with LOCK.open("a") as lock:
        descriptor = lock.fileno()
        if args.lock_fd is not None:
            inherited = os.fstat(args.lock_fd)
            opened = os.fstat(descriptor)
            if (inherited.st_dev, inherited.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError("Inherited descriptor is not the deployment lock")
            descriptor = args.lock_fd
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        policy = Retention(ROOT, Docker())
        if args.command == "record":
            policy.register(args.project, args.containers, args.state)
            policy.cleanup([args.project])
        elif args.command == "adopt":
            containers = policy.docker.containers()
            for project in args.projects:
                names = [
                    c["Name"].lstrip("/")
                    for c in containers
                    if (c["Config"].get("Labels") or {}).get("com.docker.compose.project") == project
                    and REGISTRY_IMAGE.fullmatch(c["Config"]["Image"])
                ]
                if not names:
                    raise ValueError("No migrated containers for " + project)
                policy.register(project, names)
                print("Registered " + project, flush=True)
        else:
            policy.cleanup(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
