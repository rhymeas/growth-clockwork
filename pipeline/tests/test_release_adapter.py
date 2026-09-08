from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid

from pipeline import release_core, root_writer
from pipeline.release_adapter import AdapterError, execute_release_adapter
from pipeline.review_api import ReviewService


SCHEMA_FILES = {
    "adapter-receipt@1": "adapter-receipt.schema.json",
    "adapter-request@1": "adapter-request.schema.json",
    "human-review-action@1": "human-review-action.schema.json",
    "live-proof@1": "live-proof.schema.json",
    "outbox-review-item@1": "outbox-review-item.schema.json",
    "release-outcome@1": "release-outcome.schema.json",
    "release-package@1": "release-package.schema.json",
}


class ReleaseFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name) / "growth-workspace"
        self.workspace.mkdir()
        (self.workspace / "AGENTS.md").write_text("# fixture\n", encoding="utf-8")
        self.package_root = self.workspace / "projects" / "example"
        self.schemas = self.package_root / "schemas"
        self.schemas.mkdir(parents=True)
        repository = Path(__file__).resolve().parents[2]
        for schema_id, filename in SCHEMA_FILES.items():
            source_root = (
                repository / "contracts" / "schemas"
                if filename
                not in {"human-review-action.schema.json", "outbox-review-item.schema.json"}
                else repository / "projects" / "example" / "schemas"
            )
            shutil.copy2(source_root / filename, self.schemas / filename)
        registry = {
            "registry_version": "1.0",
            "schemas": {
                schema_id: {
                    "path": f"schemas/{filename}",
                    "sha256": self._sha256((self.schemas / filename).read_bytes()),
                }
                for schema_id, filename in SCHEMA_FILES.items()
            },
        }
        self._write_json(self.package_root / "schema-registry.json", registry)
        self.profile_path = self.package_root / "project.json"
        self.profile = {
            "profile_version": "1.0",
            "profile_revision": "example-project-profile-v2",
            "project_id": "example-project",
            "display_name": "Example Project",
            "root_role_id": "growth-clockwork-root",
            "schema_registry": "schema-registry.json",
            "product_motion": "synthetic-fixture",
            "writer": {
                "state_root": "projects/example/state",
                "allowed_roots": [
                    "clockwork",
                    "handoffs",
                    "records",
                    "staging",
                    "outbox",
                ],
                "append_only_roots": [
                    "clockwork/run-receipts",
                    "handoffs",
                    "records",
                    "staging",
                    "outbox",
                ],
                "receipt_root": "clockwork/run-receipts",
                "max_files_per_request": 64,
                "max_total_bytes": 1048576,
            },
        }
        self._write_json(self.profile_path, self.profile)
        self.artifact_id = "fixture-artifact"
        self.artifact_revision = "r1"
        self.artifact_bytes = "# Exact approved fixture\n\nNo external effect.\n".encode()
        self.artifact_sha256 = self._sha256(self.artifact_bytes)
        self.source_run_id = "RUN-FIXTURE-001"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _sha256(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _pointer(ref: str, revision: str, content: bytes) -> dict[str, str]:
        return {
            "artifact_ref": ref,
            "artifact_revision": revision,
            "artifact_sha256": hashlib.sha256(content).hexdigest(),
        }

    def _apply_writer(self, run_id: str, writes: list[dict[str, object]]) -> dict:
        request = {
            "writer_request_version": "1.0",
            "project_id": self.profile["project_id"],
            "project_profile_revision": self.profile["profile_revision"],
            "run_id": run_id,
            "idempotency_key": str(uuid.uuid5(uuid.NAMESPACE_URL, run_id)),
            "requested_by": self.profile["root_role_id"],
            "writes": writes,
        }
        request_path = self.workspace / f"{run_id}.request.json"
        self._write_json(request_path, request)
        return root_writer.apply_request(
            self.workspace, self.profile_path, request_path
        )

    @staticmethod
    def _write_item(path: str, content: bytes, media_type: str) -> dict[str, object]:
        return {
            "path": path,
            "mode": "create",
            "content": content.decode("utf-8"),
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "expected_sha256": None,
            "media_type": media_type,
        }

    def _review_bundle(
        self,
        *,
        receipt: bool = True,
        review_project_id: str | None = None,
    ) -> None:
        origin_ref = "records/decisions/FIX-1/final-r1.md"
        artifact_ref = f"outbox/artifacts/{self.artifact_id}/{self.artifact_revision}.md"
        review_ref = f"outbox/pending/{self.artifact_id}/{self.artifact_revision}.json"
        lineage_ref = f"outbox/lineage/{self.artifact_id}/{self.artifact_revision}.json"
        review = {
            "review_item_version": "1.0",
            "project_id": review_project_id or self.profile["project_id"],
            "project_profile_revision": self.profile["profile_revision"],
            "artifact_id": self.artifact_id,
            "artifact_revision": self.artifact_revision,
            "artifact_path": artifact_ref,
            "artifact_sha256": self.artifact_sha256,
            "title": "Fixture result",
            "type": "fixture-package",
            "preview": "Private synthetic release fixture.",
            "evidence": [],
            "quality_checks": [
                {"name": "fixture", "status": "pass", "detail": "Synthetic only"}
            ],
        }
        review_bytes = release_core._canonical_json(review)
        dummy_task = self._pointer(
            "handoffs/RUN-FIXTURE-001/FIX-1-T001.json", "task-r1", b"task"
        )
        dummy_result = self._pointer(
            "handoffs/RUN-FIXTURE-001/results/FIX-1-T001.json", "result-r1", b"result"
        )
        lineage = {
            "lineage_version": "1.0",
            "project_id": self.profile["project_id"],
            "project_profile_revision": self.profile["profile_revision"],
            "artifact_id": self.artifact_id,
            "artifact_revision": self.artifact_revision,
            "artifact_sha256": self.artifact_sha256,
            "source_run_id": self.source_run_id,
            "source_route_id": "fixture-route",
            "producer": {
                "step_id": "produce",
                "task_ref": dummy_task,
                "result_ref": dummy_result,
                "artifact_ref": self._pointer(
                    origin_ref, self.artifact_revision, self.artifact_bytes
                ),
            },
            "governance": {
                "step_id": "governance-final",
                "task_ref": dummy_task,
                "result_ref": dummy_result,
            },
            "review_manifest_ref": self._pointer(
                review_ref, self.artifact_revision, review_bytes
            ),
        }
        lineage_bytes = release_core._canonical_json(lineage)
        writes = [
            self._write_item(origin_ref, self.artifact_bytes, "text/markdown"),
            self._write_item(artifact_ref, self.artifact_bytes, "text/markdown"),
            self._write_item(review_ref, review_bytes, "application/json"),
            self._write_item(lineage_ref, lineage_bytes, "application/json"),
        ]
        if receipt:
            self._apply_writer(self.source_run_id, writes)
        else:
            state = self.package_root / "state"
            for write in writes:
                target = state / str(write["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(write["content"]), encoding="utf-8")

    def _review_action(self, action: str) -> dict:
        body: dict[str, object] = {
            "action": action,
            "project_id": self.profile["project_id"],
            "project_profile_revision": self.profile["profile_revision"],
            "artifact_id": self.artifact_id,
            "artifact_revision": self.artifact_revision,
            "artifact_sha256": self.artifact_sha256,
        }
        if action == "decline":
            body["reason"] = "Fixture declined"
        if action == "note":
            body["note"] = "Revise the fixture"
        record, replay = ReviewService(self.workspace).review_action(body)
        self.assertFalse(replay)
        return record

    def _approved_package(self) -> dict:
        self._review_bundle()
        self._review_action("approve")
        return release_core.create_release_package(
            self.workspace,
            self.profile_path,
            artifact_id=self.artifact_id,
            artifact_revision=self.artifact_revision,
            artifact_sha256=self.artifact_sha256,
        )

    def test_complete_approved_fixture_cycle_is_exact_and_separate(self) -> None:
        packaged = self._approved_package()
        self.assertEqual(packaged["status"], "packaged")
        self.assertFalse(packaged["package"]["contains_credentials"])
        self.assertFalse(packaged["package"]["external_side_effects"])

        executed = execute_release_adapter(
            self.workspace,
            self.profile_path,
            release_package=packaged["release_package"],
            adapter_id="local-filesystem-synthetic@1",
            target="simulated-live",
            requested_at="2026-08-31T19:00:00Z",
        )

        self.assertEqual(executed["status"], "completed")
        self.assertFalse(executed["external_side_effects"])
        self.assertFalse(executed["publicly_live"])
        output = self.package_root / "state" / executed["output_artifact"]["artifact_ref"]
        self.assertEqual(output.read_bytes(), self.artifact_bytes)
        receipt = json.loads(
            (self.package_root / "state" / executed["adapter_receipt"]["artifact_ref"]).read_text()
        )
        proof = json.loads(
            (self.package_root / "state" / executed["live_proof"]["artifact_ref"]).read_text()
        )
        outcome = json.loads(
            (self.package_root / "state" / executed["outcome"]["artifact_ref"]).read_text()
        )
        self.assertEqual(receipt["output_artifact"]["artifact_sha256"], self.artifact_sha256)
        self.assertEqual(proof["proof_scope"], "local-fixture-only")
        self.assertFalse(proof["publicly_live"])
        self.assertEqual(outcome["metrics"], [])
        self.assertFalse(outcome["causal_claim"])
        self.assertEqual(len(executed["writer_receipt"]["writes"]), 5)

    def test_package_and_adapter_replays_are_idempotent(self) -> None:
        first = self._approved_package()
        second = release_core.create_release_package(
            self.workspace,
            self.profile_path,
            artifact_id=self.artifact_id,
            artifact_revision=self.artifact_revision,
            artifact_sha256=self.artifact_sha256,
        )
        self.assertEqual(first["release_package"], second["release_package"])
        self.assertTrue(second["idempotent_replay"])
        first_execution = execute_release_adapter(
            self.workspace,
            self.profile_path,
            release_package=first["release_package"],
            adapter_id="local-filesystem-synthetic@1",
            target="preview",
            requested_at="2026-08-31T19:10:00Z",
        )
        second_execution = execute_release_adapter(
            self.workspace,
            self.profile_path,
            release_package=first["release_package"],
            adapter_id="local-filesystem-synthetic@1",
            target="preview",
            requested_at="2026-08-31T19:10:00Z",
        )
        self.assertFalse(first_execution["idempotent_replay"])
        self.assertTrue(second_execution["idempotent_replay"])
        self.assertEqual(first_execution["writer_receipt"], second_execution["writer_receipt"])

    def test_hash_mismatch_fails_closed(self) -> None:
        self._review_bundle()
        self._review_action("approve")
        with self.assertRaisesRegex(release_core.ReleaseError, "does not match pending"):
            release_core.create_release_package(
                self.workspace,
                self.profile_path,
                artifact_id=self.artifact_id,
                artifact_revision=self.artifact_revision,
                artifact_sha256="0" * 64,
            )

    def test_cross_project_pending_manifest_fails_closed(self) -> None:
        self._review_bundle(review_project_id="another-project")
        with self.assertRaisesRegex(release_core.ReleaseError, "another project"):
            release_core.create_release_package(
                self.workspace,
                self.profile_path,
                artifact_id=self.artifact_id,
                artifact_revision=self.artifact_revision,
                artifact_sha256=self.artifact_sha256,
            )

    def test_symlinked_reviewed_artifact_fails_closed(self) -> None:
        self._review_bundle()
        self._review_action("approve")
        artifact = (
            self.package_root
            / "state"
            / "outbox"
            / "artifacts"
            / self.artifact_id
            / f"{self.artifact_revision}.md"
        )
        outside = self.workspace / "outside.md"
        outside.write_bytes(self.artifact_bytes)
        artifact.unlink()
        artifact.symlink_to(outside)
        with self.assertRaisesRegex(release_core.ReleaseError, "regular file safely"):
            release_core.create_release_package(
                self.workspace,
                self.profile_path,
                artifact_id=self.artifact_id,
                artifact_revision=self.artifact_revision,
                artifact_sha256=self.artifact_sha256,
            )

    def test_unreceipted_review_bundle_fails_closed(self) -> None:
        self._review_bundle(receipt=False)
        self._review_action("approve")
        with self.assertRaisesRegex(release_core.ReleaseError, "receipt directory is missing"):
            release_core.create_release_package(
                self.workspace,
                self.profile_path,
                artifact_id=self.artifact_id,
                artifact_revision=self.artifact_revision,
                artifact_sha256=self.artifact_sha256,
            )

    def test_tampered_completed_receipt_fails_closed(self) -> None:
        self._review_bundle()
        self._review_action("approve")
        receipt = next(
            (
                self.package_root
                / "state"
                / "clockwork"
                / "run-receipts"
                / self.source_run_id
            ).glob("writer-*.json")
        )
        value = json.loads(receipt.read_text(encoding="utf-8"))
        value["request_sha256"] = "0" * 64
        self._write_json(receipt, value)
        with self.assertRaisesRegex(release_core.ReleaseError, "cannot be reconstructed"):
            release_core.create_release_package(
                self.workspace,
                self.profile_path,
                artifact_id=self.artifact_id,
                artifact_revision=self.artifact_revision,
                artifact_sha256=self.artifact_sha256,
            )

    def test_decline_never_creates_release_package(self) -> None:
        self._review_bundle()
        self._review_action("decline")
        with self.assertRaisesRegex(release_core.ReleaseError, "only an exact human approve"):
            release_core.create_release_package(
                self.workspace,
                self.profile_path,
                artifact_id=self.artifact_id,
                artifact_revision=self.artifact_revision,
                artifact_sha256=self.artifact_sha256,
            )

    def test_note_never_creates_release_package(self) -> None:
        self._review_bundle()
        self._review_action("note")
        with self.assertRaisesRegex(release_core.ReleaseError, "only an exact human approve"):
            release_core.create_release_package(
                self.workspace,
                self.profile_path,
                artifact_id=self.artifact_id,
                artifact_revision=self.artifact_revision,
                artifact_sha256=self.artifact_sha256,
            )

    def test_synthetic_adapter_rejects_non_fixture_project(self) -> None:
        packaged = self._approved_package()
        self.profile["product_motion"] = "product-led-software"
        self._write_json(self.profile_path, self.profile)
        with self.assertRaisesRegex(AdapterError, "requires a synthetic-fixture"):
            execute_release_adapter(
                self.workspace,
                self.profile_path,
                release_package=packaged["release_package"],
                adapter_id="local-filesystem-synthetic@1",
                target="simulated-live",
                requested_at="2026-08-31T19:20:00Z",
            )

    def test_unregistered_adapter_and_real_target_fail_closed(self) -> None:
        packaged = self._approved_package()
        with self.assertRaisesRegex(AdapterError, "not registered"):
            execute_release_adapter(
                self.workspace,
                self.profile_path,
                release_package=packaged["release_package"],
                adapter_id="publisher@1",
                target="simulated-live",
                requested_at="2026-08-31T19:30:00Z",
            )
        with self.assertRaisesRegex(AdapterError, "target must be"):
            execute_release_adapter(
                self.workspace,
                self.profile_path,
                release_package=packaged["release_package"],
                adapter_id="local-filesystem-synthetic@1",
                target="public-live",
                requested_at="2026-08-31T19:30:00Z",
            )


if __name__ == "__main__":
    unittest.main()
