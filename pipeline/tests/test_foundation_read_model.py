from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pipeline.review_api import ReviewService


class FoundationReadModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.package = self.workspace / "projects" / "alpha"
        self.package.mkdir(parents=True)
        self.profile_path = self.package / "project.json"
        self.profile = {"project_id": "alpha", "profile_revision": "alpha-v1"}
        self.service = ReviewService.__new__(ReviewService)
        self.service.workspace = self.workspace
        self.source = self.workspace / "reference.md"
        self.markdown = "# Candidate wording\n\nNot approved.\n"
        self.source.write_text(self.markdown, encoding="utf-8")
        self.config = {
            "project_id": "alpha", "project_profile_revision": "alpha-v1",
            "categories": [{
                "id": "facts", "title": "Product facts",
                "status_label": "Candidates — not approved",
                "summary": "Reference wording only.",
                "documents": [{
                    "label": "Candidate wording", "path": "reference.md",
                    "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
                }],
            }],
        }

    def write_config(self) -> None:
        (self.package / "foundation.json").write_text(json.dumps(self.config), encoding="utf-8")

    def read(self) -> list[dict]:
        return self.service._foundation(self.profile_path, self.profile)

    def test_project_desk_returns_exact_reference_bytes_without_writes_or_identifiers(self) -> None:
        self.write_config()
        before = {path.relative_to(self.workspace): path.read_bytes()
                  for path in self.workspace.rglob("*") if path.is_file()}
        with patch("pipeline.review_api.ProjectStartService") as project_start:
            project_start.return_value.project_desk.return_value = {"phase": "context_blocked"}
            with patch.object(self.service, "_selected_profile", return_value=(self.profile_path, self.profile)):
                result = self.service.project_desk("alpha")
        item = result["foundation"][0]
        self.assertEqual(item["status"], "reference")
        self.assertEqual(item["status_label"], "Candidates — not approved")
        self.assertEqual(item["documents"], [{"label": "Candidate wording", "markdown": self.markdown}])
        self.assertEqual(before, {path.relative_to(self.workspace): path.read_bytes()
                                 for path in self.workspace.rglob("*") if path.is_file()})

    def test_missing_configuration_does_not_borrow_workspace_or_other_project_sources(self) -> None:
        self.assertEqual(self.read(), [])
        self.write_config()
        other = self.workspace / "projects" / "beta"
        other.mkdir()
        self.assertEqual(self.service._foundation(other / "project.json", {
            "project_id": "beta", "profile_revision": "beta-v1",
        }), [])

    def test_changed_source_clears_stale_summary_and_content(self) -> None:
        self.write_config()
        self.source.write_text("Changed source; not the pinned reference.", encoding="utf-8")
        result = self.read()[0]
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["status_label"], "Reference refresh needed")
        self.assertNotEqual(result["summary"], "Reference wording only.")
        self.assertEqual(result["documents"], [])

    def test_wrong_project_or_profile_never_exposes_sources(self) -> None:
        for field, value in (("project_id", "beta"), ("project_profile_revision", "alpha-v0")):
            with self.subTest(field=field):
                previous = self.config[field]
                self.config[field] = value
                self.write_config()
                self.assertEqual(self.read()[0]["documents"], [])
                self.assertEqual(self.read()[0]["status"], "unavailable")
                self.config[field] = previous

    def test_unsafe_or_cross_project_paths_are_not_read(self) -> None:
        for path in ("../reference.md", "/reference.md", ".private/reference.md",
                     "projects/beta/reference.md", "credentials.json"):
            with self.subTest(path=path):
                self.config["categories"][0]["documents"][0]["path"] = path
                self.write_config()
                with patch.object(self.service, "_foundation_bytes", wraps=self.service._foundation_bytes) as read:
                    self.assertEqual(self.read()[0]["status"], "unavailable")
                    self.assertEqual(read.call_count, 1, "Only configuration may be read")

    def test_symlink_source_and_configuration_fail_closed(self) -> None:
        self.write_config()
        original = self.workspace / "original.md"
        self.source.rename(original)
        self.source.symlink_to(original)
        self.assertEqual(self.read()[0]["status"], "unavailable")
        configured = self.package / "foundation.json"
        saved = self.package / "saved.json"
        configured.rename(saved)
        configured.symlink_to(saved)
        self.assertEqual(self.read()[0]["status"], "unavailable")

    def test_oversized_and_invalid_utf8_sources_are_unavailable(self) -> None:
        for content in (b"x" * 65_537, b"\xff"):
            with self.subTest(length=len(content)):
                self.source.write_bytes(content)
                self.config["categories"][0]["documents"][0]["sha256"] = hashlib.sha256(content).hexdigest()
                self.write_config()
                self.assertEqual(self.read()[0]["documents"], [])
                self.assertEqual(self.read()[0]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
