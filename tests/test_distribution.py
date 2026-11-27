import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import unittest

import yaml

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
CHART = DEPLOY / "helm" / "relayna"


class DistributionTests(unittest.TestCase):
    def test_exact_source_revisions(self):
        sources = json.loads((DEPLOY / "sources.json").read_text(encoding="utf-8"))
        self.assertEqual(set(sources), {"gateway", "sdk", "studio", "spooltail", "cell"})
        for component in ("sdk", "studio"):
            self.assertEqual(sources[component]["repository"], f"https://github.com/Relayna/relayna-{component}.git")
        for name, source in sources.items():
            with self.subTest(component=name):
                self.assertRegex(source["commit"], r"^[a-f0-9]{40}$")

    def test_studio_images_use_the_studio_source(self):
        images = runpy.run_path(str(DEPLOY / "build.py"))["IMAGES"]
        self.assertEqual(images["sdk"][:2], ("sdk", "Dockerfile.reference"))
        self.assertEqual(images["studio-backend"][:2], ("studio", "studio/backend/Dockerfile"))
        self.assertEqual(images["studio"][:2], ("studio", "apps/studio/Dockerfile"))

    @unittest.skipUnless(shutil.which("docker"), "Docker is needed to render Compose")
    def test_compose_profile_keeps_internal_ports_private(self):
        version = subprocess.run(["docker", "compose", "version"], capture_output=True, check=False)
        if version.returncode:
            self.skipTest("Docker Compose v2 is needed to render the profile")
        environment = os.environ | {
            "RELAYNA_DB_PASSWORD": "test",
            "RELAYNA_GATEWAY_ADMIN_TOKEN": "test",
            "RELAYNA_STUDIO_OPERATOR_TOKEN": "test",
        }
        spec = subprocess.check_output(
            ["docker", "compose", "-f", str(DEPLOY / "compose.yaml"), "--profile", "studio", "config", "--format", "json"],
            env=environment,
            text=True,
        )
        services = json.loads(spec)["services"]
        self.assertEqual({key for key, value in services.items() if value.get("ports")}, {"gateway", "studio"})
        for key in ("gateway", "studio"):
            self.assertTrue(all(port["host_ip"] == "127.0.0.1" for port in services[key]["ports"]))
        self.assertEqual(services["studio-migrate"]["command"], ["alembic", "upgrade", "head"])
        self.assertEqual(services["sdk"]["environment"]["SPOOLTAIL_TARGET"], "spooltail:7443")
        self.assertEqual(services["studio-backend"]["environment"]["RELAYNA_STUDIO_CAPABILITY_REFRESH_ALLOWED_HOSTS"], "sdk,gateway")

    @unittest.skipUnless(shutil.which("helm"), "Helm is needed to render the chart")
    def test_helm_profiles_render_persistent_single_node_services(self):
        for enabled in ("true", "false"):
            with self.subTest(studio=enabled):
                rendered = subprocess.check_output(
                    ["helm", "template", "reference", str(CHART), "--set", f"studio.enabled={enabled}"],
                    text=True,
                )
                resources = {
                    (doc["kind"], doc["metadata"]["name"]): doc
                    for doc in yaml.safe_load_all(rendered)
                    if doc is not None
                }
                claims = {name for kind, name in resources if kind == "PersistentVolumeClaim"}
                self.assertEqual(claims, {"reference-postgres", "reference-spooltail"})
                self.assertEqual(("Service", "reference-studio") in resources, enabled == "true")
                for (kind, _), resource in resources.items():
                    if kind == "Deployment":
                        self.assertEqual(resource["spec"]["replicas"], 1)
                        self.assertFalse(resource["spec"]["template"]["spec"]["enableServiceLinks"])
                    if kind == "Service":
                        self.assertEqual(resource["spec"].get("type", "ClusterIP"), "ClusterIP")
                if enabled == "true":
                    studio = resources["Deployment", "reference-studio-backend"]
                    pod = studio["spec"]["template"]["spec"]
                    init = pod["initContainers"]
                    self.assertEqual(init[0]["command"], ["alembic", "upgrade", "head"])
                    environment = {item["name"]: item.get("value") for item in pod["containers"][0]["env"]}
                    self.assertEqual(environment["RELAYNA_STUDIO_CAPABILITY_REFRESH_ALLOWED_HOSTS"], "reference-sdk,reference-gateway")
