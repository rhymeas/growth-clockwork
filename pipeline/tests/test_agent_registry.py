from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from pipeline.agent_registry import AgentRegistryError, load_agent_registry
from pipeline.root_writer import load_project_profile


WORKSPACE = Path(__file__).resolve().parents[2]


class AgentRegistryTests(unittest.TestCase):
    def test_example_profile_resolves_the_portable_registry(self) -> None:
        profile_path = WORKSPACE / "projects/example/project.json"
        profile = load_project_profile(profile_path)
        registry = load_agent_registry(
            WORKSPACE, profile_path, profile["_agent_registry"]
        )

        self.assertEqual(len(registry.roles), 19)
        self.assertEqual(
            set(registry.routes),
            {
                "audience-deep-research",
                "content-channel",
                "demand-acquisition",
                "launch-readiness",
                "lifecycle-retention",
                "market-positioning",
                "marketing-operations",
                "measurement-learning",
                "organic-audience-community",
                "product-funnel-experiment",
                "research-evidence",
            },
        )
        self.assertIn("growth-product-cro", registry.roles)
        self.assertEqual(
            registry.routes["product-funnel-experiment"].governance_node,
            "governance-final",
        )

    def _copy_workspace(self) -> tuple[tempfile.TemporaryDirectory, Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        workspace = Path(temporary.name) / "workspace"
        workspace.mkdir()
        (workspace / "AGENTS.md").write_text("# Test\n", encoding="utf-8")
        shutil.copytree(WORKSPACE / "agents", workspace / "agents")
        shutil.copytree(
            WORKSPACE / "projects/example", workspace / "projects/example"
        )
        return temporary, workspace, workspace / "projects/example/project.json"

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_tampered_role_contract_is_rejected(self) -> None:
        temporary, workspace, profile_path = self._copy_workspace()
        with temporary:
            role_path = workspace / "agents/roles/core/growth-coordinator.json"
            role_path.write_text("{}\n", encoding="utf-8")
            profile = load_project_profile(profile_path)

            with self.assertRaisesRegex(AgentRegistryError, "hash mismatch"):
                load_agent_registry(workspace, profile_path, profile["_agent_registry"])

    def test_cycle_in_re_pinned_route_is_rejected(self) -> None:
        temporary, workspace, profile_path = self._copy_workspace()
        with temporary:
            route_path = workspace / "agents/routes/research-evidence.json"
            route = json.loads(route_path.read_text(encoding="utf-8"))
            route["nodes"][0]["depends_on"] = ["governance-final"]
            self._write_json(route_path, route)
            registry_path = workspace / "agents/registry.json"
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            entry = next(
                item
                for item in registry["routes"]
                if item["route_id"] == "research-evidence"
            )
            entry["sha256"] = hashlib.sha256(route_path.read_bytes()).hexdigest()
            self._write_json(registry_path, registry)
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            profile["agent_registry"]["artifact_sha256"] = hashlib.sha256(
                registry_path.read_bytes()
            ).hexdigest()
            self._write_json(profile_path, profile)
            loaded = load_project_profile(profile_path)

            with self.assertRaisesRegex(AgentRegistryError, "dependency cycle"):
                load_agent_registry(workspace, profile_path, loaded["_agent_registry"])

    def test_enabled_route_cannot_use_a_role_missing_from_profile(self) -> None:
        temporary, workspace, profile_path = self._copy_workspace()
        with temporary:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            profile["role_ids"].remove("growth-product-cro")
            profile["mandatory_roles"] = [
                role
                for role in profile["mandatory_roles"]
                if role != "growth-product-cro"
            ]
            self._write_json(profile_path, profile)
            loaded = load_project_profile(profile_path)

            with self.assertRaisesRegex(AgentRegistryError, "not enabled"):
                load_agent_registry(workspace, profile_path, loaded["_agent_registry"])


if __name__ == "__main__":
    unittest.main()
