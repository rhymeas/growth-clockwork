from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from pipeline.code_graph import assess_code_graph_index


class CodeGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.profile_root = Path(self.temporary.name) / "alpha"
        schemas = self.profile_root / "schemas"
        schemas.mkdir(parents=True)
        source = (
            Path(__file__).resolve().parents[2]
            / "contracts"
            / "schemas"
            / "code-graph-index.schema.json"
        )
        content = source.read_bytes()
        (schemas / source.name).write_bytes(content)
        (self.profile_root / "schema-registry.json").write_text(
            json.dumps(
                {
                    "registry_version": "1.0",
                    "schemas": {
                        "code-graph-index@1": {
                            "path": f"schemas/{source.name}",
                            "sha256": hashlib.sha256(content).hexdigest(),
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        self.profile = self.profile_root / "project.json"
        self.profile.write_text(
            json.dumps(
                {
                    "profile_version": "1.0",
                    "profile_revision": "alpha-profile-v1",
                    "project_id": "alpha-project",
                    "schema_registry": "schema-registry.json",
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def index(self) -> dict[str, object]:
        return {
            "project_id": "alpha-project",
            "project_profile_revision": "alpha-profile-v1",
            "index_version": "1.0",
            "index_id": "CG-alpha-main",
            "index_revision": "r1",
            "purpose": "code_navigation_only",
            "repository_root": "source",
            "source_revision": "0123456789abcdef0123456789abcdef01234567",
            "source_state": "clean_commit",
            "adapter": {
                "id": "scip",
                "version": "0.6.1",
                "license_id": "Apache-2.0",
                "source_url": "https://github.com/scip-code/scip",
            },
            "graph_format": "scip",
            "coverage": {
                "files_indexed": 12,
                "symbols_indexed": 180,
                "edges_indexed": 420,
                "languages": ["python"],
            },
            "artifacts": [
                {
                    "artifact_ref": "code-intelligence/indexes/alpha.scip",
                    "artifact_revision": "r1",
                    "artifact_sha256": "2" * 64,
                }
            ],
            "limitations": ["Dynamic dispatch is not fully resolved."],
            "external_upload": False,
            "contains_secrets": False,
            "created_at": "2026-08-31T12:00:00Z",
        }

    def gitnexus_index(self) -> dict[str, object]:
        index = self.index()
        index["index_id"] = "CG-alpha-gitnexus"
        index["adapter"] = {
            "id": "gitnexus",
            "version": "1.6.10",
            "license_id": "PolyForm-Noncommercial-1.0.0",
            "source_url": "https://github.com/abhigyanpatwari/GitNexus",
        }
        index["graph_format"] = "gitnexus-ladybugdb"
        index["artifacts"] = [
            {
                "artifact_ref": "code-intelligence/indexes/alpha.gitnexus",
                "artifact_revision": "r1",
                "artifact_sha256": "3" * 64,
            }
        ]
        index["limitations"] = [
            "Synthetic contract fixture; GitNexus was not installed or executed."
        ]
        return index

    def test_clean_scip_index_passes(self) -> None:
        report = assess_code_graph_index(self.profile, self.index())
        self.assertEqual(report["status"], "pass")
        self.assertFalse(report["canonical_memory"])

    def test_license_mismatch_blocks(self) -> None:
        index = self.index()
        index["adapter"]["license_id"] = "MIT"  # type: ignore[index]
        report = assess_code_graph_index(self.profile, index)
        self.assertEqual(report["status"], "block")
        self.assertTrue(any("Apache-2.0" in item for item in report["blockers"]))

    def test_gitnexus_receipt_surfaces_noncommercial_only_restriction(self) -> None:
        report = assess_code_graph_index(self.profile, self.gitnexus_index())

        self.assertEqual(report["status"], "warn")
        self.assertEqual(report["blockers"], [])
        self.assertEqual(report["license_restrictions"], ["noncommercial-only"])
        warning = " ".join(report["warnings"])
        self.assertIn("PolyForm Noncommercial 1.0.0", warning)
        self.assertIn("noncommercial-only", warning)
        self.assertNotIn("commercial-open-source", warning.casefold())

    def test_gitnexus_license_mismatch_blocks(self) -> None:
        index = self.gitnexus_index()
        index["adapter"]["license_id"] = "MIT"  # type: ignore[index]
        report = assess_code_graph_index(self.profile, index)

        self.assertEqual(report["status"], "block")
        self.assertTrue(
            any(
                "PolyForm-Noncommercial-1.0.0" in item
                for item in report["blockers"]
            )
        )

    def test_dirty_index_is_navigation_only_warning(self) -> None:
        index = self.index()
        index["source_state"] = "dirty_worktree"
        report = assess_code_graph_index(self.profile, index)
        self.assertEqual(report["status"], "warn")

    def test_external_upload_is_rejected_by_contract(self) -> None:
        index = self.index()
        index["external_upload"] = True
        report = assess_code_graph_index(self.profile, index)
        self.assertEqual(report["status"], "block")


if __name__ == "__main__":
    unittest.main()
