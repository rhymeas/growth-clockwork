from __future__ import annotations

import json
import re
import unittest
from pathlib import Path, PurePosixPath
from pipeline.package_export import candidate_files


WORKSPACE = Path(__file__).resolve().parents[2]
MANIFEST_PATH = WORKSPACE / "open-source-package.json"


class OpenSourcePackageBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_manifest_shape_and_non_release_status(self) -> None:
        manifest = self.manifest
        self.assertEqual(
            set(manifest),
            {
                "contract_id",
                "package_name",
                "boundary_revision",
                "status",
                "future_license",
                "package_root_files",
                "include_entries",
                "exclude_entries",
                "export_rules",
            },
        )
        self.assertEqual(
            manifest["contract_id"], "growth-clockwork-open-source-package@1"
        )
        self.assertEqual(manifest["package_name"], "growth-clockwork")
        self.assertFalse(manifest["status"]["current_repository_is_open_source"])
        self.assertFalse(manifest["status"]["package_is_published"])
        self.assertEqual(
            manifest["status"]["classification"], "licensed_export_candidate"
        )

        license_boundary = manifest["future_license"]
        self.assertEqual(license_boundary["recommended_spdx_id"], "Apache-2.0")
        self.assertEqual(
            license_boundary["decision_status"],
            "selected_for_export_package",
        )
        self.assertFalse(license_boundary["applies_to_current_repository"])
        self.assertTrue(license_boundary["license_file_exists_for_package"])

    def test_paths_are_safe_normalized_and_unique(self) -> None:
        for key in ("include_entries", "exclude_entries"):
            paths = [entry["path"] for entry in self.manifest[key]]
            self.assertEqual(len(paths), len(set(paths)), f"duplicate path in {key}")
            for value in paths:
                self.assertIsInstance(value, str)
                self.assertTrue(value)
                self.assertNotIn("\\", value)
                self.assertNotIn("*", value)
                self.assertNotIn("?", value)
                path = PurePosixPath(value)
                self.assertFalse(path.is_absolute())
                self.assertNotIn("..", path.parts)
                self.assertNotIn(".", path.parts)
                self.assertEqual(path.as_posix(), value.rstrip("/"))

        mappings = self.manifest["package_root_files"]
        targets = [entry["path"] for entry in mappings]
        self.assertEqual(len(targets), len(set(targets)))
        self.assertEqual(set(targets), {
            "README.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md",
            "THIRD-PARTY.md", "SBOM.cdx.json", ".gitignore", ".gitleaks.toml",
            "runtime/permissions.example.json", "runtime/postiz.example.json",
            "runtime/media-processing.example.json", "runtime/ga4.example.json",
            "runtime/remote-access.example.json",
        })
        for entry in mappings:
            self.assertEqual(set(entry), {"source", "path"})
            self.assertTrue((WORKSPACE / entry["source"]).is_file())
            for value in entry.values():
                path = PurePosixPath(value)
                self.assertFalse(path.is_absolute())
                self.assertNotIn("..", path.parts)
                self.assertNotIn("\\", value)

        rules = self.manifest["export_rules"]
        self.assertTrue(rules["relative_paths_only"])
        self.assertTrue(rules["exclude_entries_win"])
        self.assertFalse(rules["follow_symlinks"])
        self.assertTrue(rules["require_all_include_entries_to_exist"])

    def test_required_reusable_roots_exist(self) -> None:
        includes = {entry["path"] for entry in self.manifest["include_entries"]}
        required = {
            "pipeline",
            "agents",
            "contracts",
            "dashboard",
            "projects/example",
            "skills/growth-clockwork",
            "docs/open-source-package-boundary.md",
            "open-source-package.json",
        }
        self.assertTrue(required.issubset(includes))

        for relative in includes:
            self.assertTrue(
                (WORKSPACE / relative).exists(), f"missing include path: {relative}"
            )

        example_entry = next(
            entry
            for entry in self.manifest["include_entries"]
            if entry["path"] == "projects/example"
        )
        self.assertEqual(example_entry["kind"], "synthetic_example_project")

    def test_private_and_runtime_roots_are_explicitly_excluded(self) -> None:
        excludes = {entry["path"] for entry in self.manifest["exclude_entries"]}
        required = {
            "projects/" + "mav" + "ery",
            "projects/example/state",
            "facts",
            "policy",
            "research",
            "content",
            "metrics",
            "published",
            "evidence",
            "providers",
            "templates",
            ".github",
            "dashboard/node_modules",
            "dashboard/dist",
            "dashboard/design",
        }
        self.assertTrue(required.issubset(excludes))

        categories = {
            entry["category"] for entry in self.manifest["exclude_entries"]
        }
        self.assertIn("product_specific_project_profile_and_state", categories)
        self.assertIn("runtime_outputs", categories)
        self.assertIn("private_marketing_context", categories)
        self.assertIn("private_research", categories)

    def test_example_project_is_synthetic_and_non_publishable(self) -> None:
        profile = json.loads(
            (WORKSPACE / "projects/example/project.json").read_text(encoding="utf-8")
        )
        self.assertEqual(profile["project_id"], "example-project")
        self.assertEqual(profile["product_motion"], "synthetic-fixture")

        manifest_path = (
            WORKSPACE
            / "projects/example"
            / profile["project_context"]["artifact_ref"]
        )
        context_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertFalse(
            context_manifest["authority"]["publish_credentials_available"]
        )
        for section in context_manifest["sections"]:
            section_path = WORKSPACE / "projects/example" / section["artifact_ref"]
            payload = json.loads(section_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["synthetic"], section["artifact_ref"])
            self.assertTrue(payload["fixture_only"], section["artifact_ref"])
            self.assertFalse(payload["publishable"], section["artifact_ref"])

    def test_expanded_candidate_cannot_cross_private_boundary(self) -> None:
        exported = self._expanded_candidate_files()
        excluded = [
            PurePosixPath(entry["path"])
            for entry in self.manifest["exclude_entries"]
        ]
        rules = self.manifest["export_rules"]
        forbidden_names = set(rules["forbidden_file_names"])
        forbidden_suffixes = tuple(rules["forbidden_file_suffixes"])

        self.assertTrue(exported, "candidate export is empty")
        for relative in exported:
            self.assertFalse(
                self._is_within_any(relative, excluded),
                f"excluded path entered candidate export: {relative}",
            )
            self.assertNotIn(relative.name, forbidden_names)
            self.assertFalse(relative.name.endswith(forbidden_suffixes))

        expected_files = {
            PurePosixPath("pipeline/root_writer.py"),
            PurePosixPath("agents/registry.json"),
            PurePosixPath("contracts/schemas/task-envelope.schema.json"),
            PurePosixPath("dashboard/src/App.tsx"),
            PurePosixPath("projects/example/project.json"),
            PurePosixPath("skills/growth-clockwork/SKILL.md"),
        }
        self.assertTrue(expected_files.issubset(exported))
        self.assertNotIn(PurePosixPath("projects/example/state"), exported)

    def test_exported_text_has_no_product_specific_runtime_references(self) -> None:
        exported = self._expanded_candidate_files()
        explicit_boundary_files = {
            PurePosixPath("open-source-package.json"),
            PurePosixPath("docs/open-source-package-boundary.md"),
        }
        forbidden_fragments = tuple(
            fragment.lower()
            for fragment in self.manifest["export_rules"][
                "forbidden_text_fragments"
            ]
        )
        violations: list[str] = []
        for relative in sorted(exported - explicit_boundary_files):
            try:
                content = (WORKSPACE / relative).read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            lowered = content.lower()
            for fragment in forbidden_fragments:
                if fragment in lowered:
                    violations.append(f"{relative}: {fragment}")

        self.assertEqual(violations, [])

    def test_skill_file_references_stay_inside_candidate_export(self) -> None:
        exported = self._expanded_candidate_files()
        skill_root = WORKSPACE / "skills/growth-clockwork"
        skill_path = skill_root / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        references = set(
            re.findall(
                r"`((?:[A-Za-z0-9_.<>-]+/)+[A-Za-z0-9_.<>-]+\.(?:md|json))`",
                content,
            )
        )
        references.update(
            re.findall(r"\]\(([^)]+\.(?:md|json))\)", content)
        )

        checked: set[PurePosixPath] = set()
        for reference in references:
            if "<" in reference or ">" in reference:
                continue
            candidates = (WORKSPACE / reference, skill_root / reference)
            existing = next((path for path in candidates if path.is_file()), None)
            self.assertIsNotNone(existing, f"missing skill reference: {reference}")
            assert existing is not None
            relative = PurePosixPath(existing.relative_to(WORKSPACE).as_posix())
            self.assertIn(relative, exported, f"skill reference is not exported: {relative}")
            checked.add(relative)

        self.assertEqual(
            checked,
            {
                PurePosixPath("agents/README.md"),
                PurePosixPath("pipeline/README.md"),
                PurePosixPath("skills/growth-clockwork/references/runtime.md"),
            },
        )

    @classmethod
    def _expanded_candidate_files(cls) -> set[PurePosixPath]:
        return candidate_files(WORKSPACE, cls.manifest)

    @staticmethod
    def _is_within_any(path: PurePosixPath, roots: list[PurePosixPath]) -> bool:
        return any(path == root or root in path.parents for root in roots)


if __name__ == "__main__":
    unittest.main()
