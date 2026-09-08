from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import uuid

from pipeline import root_writer
from pipeline.coordinator import prepare_run
from pipeline.review_api import ReviewService
from pipeline.rework import ReworkError, prepare_rework
from pipeline.run_engine import accept_result
from pipeline.tests.professional_fixture import professional_deliverable_content
from pipeline.tests.operating_fixture import operating_contract


SOURCE_WORKSPACE = Path(__file__).resolve().parents[2]


class ReworkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name) / "workspace"
        self.workspace.mkdir()
        (self.workspace / "AGENTS.md").write_text("# Test\n", encoding="utf-8")
        shutil.copytree(SOURCE_WORKSPACE / "agents", self.workspace / "agents")
        shutil.copytree(SOURCE_WORKSPACE / "contracts", self.workspace / "contracts")
        shutil.copytree(
            SOURCE_WORKSPACE / "projects/example",
            self.workspace / "projects/example",
        )
        self.profile = self.workspace / "projects/example/project.json"
        self.state = self.workspace / "projects/example/state"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _temporary_json(self, value: object) -> Path:
        path = Path(self.temporary.name) / f"input-{uuid.uuid4()}.json"
        self._write_json(path, value)
        return path

    def _manifest(self, pointer: dict[str, str]) -> dict[str, object]:
        return json.loads(
            (self.state / pointer["artifact_ref"]).read_text(encoding="utf-8")
        )

    def _plan(self) -> dict[str, object]:
        profile = json.loads(self.profile.read_text(encoding="utf-8"))
        context_path = self.profile.parent / profile["project_context"]["artifact_ref"]
        context = json.loads(context_path.read_text(encoding="utf-8"))
        facts = next(item for item in context["sections"] if item["kind"] == "facts")
        return {
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
            "plan_version": "1.0",
            "request_id": str(uuid.uuid4()),
            "work_item_id": "EX-980",
            "run_mode": "fixture",
            "objective": "Produce one bounded synthetic evidence packet for exact human review.",
            "project_context": profile["project_context"],
            "diagnosis": {
                "problem": "The fixture project needs one traceable evidence packet.",
                "controllable_bottleneck": "The decision lacks a revision-bound synthetic result.",
                "evidence_refs": [
                    {
                        "artifact_ref": facts["artifact_ref"],
                        "artifact_revision": facts["artifact_revision"],
                        "artifact_sha256": facts["artifact_sha256"],
                    }
                ],
                "assumptions": ["All inputs and outputs are synthetic fixtures."],
                "confidence": "medium",
            },
            "route_decision": {
                "selected_route_id": "research-evidence",
                "rationale": "This route is the smallest complete fixture for the evidence decision.",
                "alternatives": [
                    {
                        "route_id": "market-positioning",
                        "reason_not_selected": "Positioning is premature before evidence synthesis.",
                    }
                ],
                "step_activation": [],
            },
            "operating_contract": operating_contract(
                profile, "2026-09-01T08:00:00Z"
            ),
            "requested_at": "2026-09-01T08:00:00Z",
        }

    def _submission(
        self,
        manifest: dict[str, object],
        step_id: str,
        contract_id: str,
        artifact_ref: str,
        *,
        content: str = "synthetic result\n",
        review_candidate: dict[str, object] | None = None,
    ) -> dict[str, object]:
        task_entry = next(
            item for item in manifest["tasks"] if item["step_id"] == step_id
        )
        task = json.loads(
            (self.state / task_entry["task_ref"]).read_text(encoding="utf-8")
        )
        evidence_refs = [dict(pointer) for pointer in task["input_artifacts"][:1]]
        content = professional_deliverable_content(
            self.workspace,
            self.profile,
            task_entry,
            task,
            contract_id,
            evidence_refs=evidence_refs,
            fixture_note=content,
        )
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        result = {
            "project_id": manifest["project_id"],
            "project_profile_revision": manifest["project_profile_revision"],
            "result_version": "2.0",
            "run_id": manifest["run_id"],
            "task_id": task_entry["task_id"],
            "task_sha256": task_entry["task_sha256"],
            "step_id": step_id,
            "role_id": task["assigned_role"],
            "status": "completed",
            "output_artifacts": [
                {
                    "artifact_ref": artifact_ref,
                    "artifact_revision": "r1",
                    "artifact_sha256": digest,
                    "contract_id": contract_id,
                }
            ],
            "evidence_artifacts": evidence_refs,
            "checks": [
                {
                    "name": "fixture-contract",
                    "result": "pass",
                    "evidence_ref": evidence_refs[0],
                }
            ],
            "attempts": 1,
            "usage": {
                "duration_ms": 25,
                "input_tokens": 10,
                "output_tokens": 20,
                "variable_external_cost_eur": 0,
            },
            "provenance": {
                "provider": "fixture",
                "model": "fixture-worker",
                "prompt_version": "fixture-v1",
                "contract_version": "result-envelope@2",
                "input_revision": task_entry["task_sha256"],
                "output_revision": "r1",
            },
            "error": None,
            "created_at": "2026-09-01T08:05:00Z",
        }
        submission: dict[str, object] = {
            "submission_version": "1.0",
            "project_id": manifest["project_id"],
            "project_profile_revision": manifest["project_profile_revision"],
            "run_id": manifest["run_id"],
            "task_id": task_entry["task_id"],
            "result": result,
            "artifacts": [
                {
                    "artifact_ref": artifact_ref,
                    "artifact_revision": "r1",
                    "artifact_sha256": digest,
                    "media_type": "application/json",
                    "content": content,
                }
            ],
        }
        if review_candidate is not None:
            submission["review_candidate"] = review_candidate
        return submission

    def _accept(self, submission: dict[str, object]) -> dict[str, object]:
        return accept_result(
            self.workspace, self.profile, self._temporary_json(submission)
        )

    def _reach_review(self, action: str = "note") -> dict[str, object]:
        prepared = prepare_run(
            self.workspace, self.profile, self._temporary_json(self._plan())
        )
        first = self._manifest(prepared["manifest"])
        after_coordinate = self._accept(
            self._submission(
                first,
                "coordinate",
                "task-set@1",
                "records/decisions/EX-980-coordinate-r1.md",
            )
        )
        second = self._manifest(after_coordinate["manifest"])
        producer_content = "# Synthetic evidence packet\n\nOriginal reviewed bytes.\n"
        producer_ref = "evidence/packets/EX-980-evidence-r1.md"
        research = self._submission(
            second,
            "research",
            "evidence-packet@1",
            producer_ref,
            content=producer_content,
        )
        producer_content = research["artifacts"][0]["content"]
        producer_pointer = research["result"]["output_artifacts"][0]
        after_research = self._accept(research)
        third = self._manifest(after_research["manifest"])
        candidate = {
            "artifact_ref": producer_ref,
            "artifact_revision": "r1",
            "artifact_sha256": producer_pointer["artifact_sha256"],
            "artifact_id": "EX-980-evidence",
            "title": "Synthetic evidence packet",
            "type": "evidence-packet",
            "preview": "A bounded synthetic finding prepared for exact human review.",
            "evidence": [{"label": "Producer output", "ref": producer_ref}],
            "quality_checks": [
                {
                    "name": "Governance fixture",
                    "status": "pass",
                    "detail": "The synthetic governance result passed.",
                }
            ],
        }
        final = self._accept(
            self._submission(
                third,
                "governance-final",
                "governance-verdict@1",
                "records/governance/EX-980-verdict-r1.md",
                review_candidate=candidate,
            )
        )
        review = json.loads(
            (self.state / final["review_item"]["artifact_ref"]).read_text(
                encoding="utf-8"
            )
        )
        request: dict[str, object] = {
            "action": action,
            "project_id": review["project_id"],
            "project_profile_revision": review["project_profile_revision"],
            "artifact_id": review["artifact_id"],
            "artifact_revision": review["artifact_revision"],
            "artifact_sha256": review["artifact_sha256"],
        }
        if action == "note":
            request["note"] = "Make the evidence limitation explicit in the opening."
        elif action == "decline":
            request["reason"] = "The direction should not continue."
        record, _ = ReviewService(self.workspace).review_action(request)
        action_ref = (
            f"outbox/review-actions/{review['artifact_id']}/"
            f"{review['artifact_revision']}.json"
        )
        return {
            "source": prepared,
            "final": final,
            "review": review,
            "record": record,
            "action_ref": action_ref,
            "producer_content": producer_content,
        }

    def test_note_prepares_full_pinned_route_with_exact_rework_inputs(self) -> None:
        setup = self._reach_review("note")
        before = (self.state / setup["review"]["artifact_path"]).read_bytes()

        result = prepare_rework(
            self.workspace, self.profile, setup["action_ref"]
        )

        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["source_run_id"], setup["source"]["run_id"])
        self.assertEqual(result["route_id"], "research-evidence")
        self.assertEqual(result["expected_artifact_revision"], "r2")
        manifest = self._manifest(result["manifest"])
        source_manifest = self._manifest(setup["final"]["manifest"])
        self.assertEqual(manifest["project_context"], source_manifest["project_context"])
        self.assertEqual(manifest["planned_steps"], source_manifest["planned_steps"])
        self.assertEqual(manifest["status"], "queued")
        self.assertFalse(manifest["publish_side_effects"])

        task_entry = manifest["tasks"][0]
        task = json.loads(
            (self.state / task_entry["task_ref"]).read_text(encoding="utf-8")
        )
        input_refs = {item["artifact_ref"] for item in task["input_artifacts"]}
        self.assertIn(setup["action_ref"], input_refs)
        self.assertIn(setup["final"]["lineage"]["artifact_ref"], input_refs)
        self.assertIn(setup["review"]["artifact_path"], input_refs)
        self.assertIn(setup["record"]["note"], task["objective"])
        self.assertIn("Expected next artifact revision: r2", task["objective"])
        self.assertFalse(task["authority"]["external_side_effects"])
        self.assertFalse(task["authority"]["may_publish"])
        self.assertEqual(
            (self.state / setup["review"]["artifact_path"]).read_bytes(), before
        )

        source_route = self.state / source_manifest["route_contract"]["artifact_ref"]
        new_route = self.state / manifest["route_contract"]["artifact_ref"]
        self.assertEqual(new_route.read_bytes(), source_route.read_bytes())
        self.assertIn(result["manifest"]["artifact_ref"], {
            item["path"] for item in result["writer_receipt"]["writes"]
        })

        advanced = self._accept(
            self._submission(
                manifest,
                "coordinate",
                "task-set@1",
                "records/decisions/EX-980-rework-coordinate-r1.md",
            )
        )
        self.assertEqual(
            [item["step_id"] for item in advanced["new_tasks"]], ["research"]
        )

    def test_same_note_replay_is_idempotent(self) -> None:
        setup = self._reach_review("note")
        first = prepare_rework(self.workspace, self.profile, setup["action_ref"])
        followup = (
            self.state
            / "outbox/review-actions"
            / setup["review"]["artifact_id"]
            / "r2.json"
        )
        followup.write_text("{}\n", encoding="utf-8")
        second = prepare_rework(self.workspace, self.profile, setup["action_ref"])

        self.assertEqual(first, second)
        run_dir = self.state / "records/runs" / first["run_id"]
        self.assertEqual(
            sorted(path.name for path in run_dir.iterdir()), ["manifest-r0001.json"]
        )

    def test_approve_action_is_rejected(self) -> None:
        setup = self._reach_review("approve")
        with self.assertRaisesRegex(ReworkError, "only a human note"):
            prepare_rework(self.workspace, self.profile, setup["action_ref"])

    def test_decline_action_is_rejected(self) -> None:
        setup = self._reach_review("decline")
        with self.assertRaisesRegex(ReworkError, "only a human note"):
            prepare_rework(self.workspace, self.profile, setup["action_ref"])

    def test_reviewed_artifact_hash_tamper_is_rejected(self) -> None:
        setup = self._reach_review("note")
        (self.state / setup["review"]["artifact_path"]).write_bytes(
            b"tampered reviewed bytes\n"
        )
        with self.assertRaisesRegex(ReworkError, "artifact bytes.*hash"):
            prepare_rework(self.workspace, self.profile, setup["action_ref"])

    def test_cross_project_action_identity_is_rejected(self) -> None:
        setup = self._reach_review("note")
        path = self.state / setup["action_ref"]
        action = json.loads(path.read_text(encoding="utf-8"))
        action["project_id"] = "other-project"
        action["project_profile_revision"] = "other-profile-v1"
        self._write_json(path, action)

        with self.assertRaisesRegex(ReworkError, "another project profile"):
            prepare_rework(self.workspace, self.profile, setup["action_ref"])

    def test_missing_action_receipt_is_rejected(self) -> None:
        setup = self._reach_review("note")
        action_id = setup["record"]["action_id"].replace("-", "")[:20]
        receipt_dir = self.state / "clockwork/run-receipts" / f"review-{action_id}"
        for path in receipt_dir.iterdir():
            path.unlink()
        receipt_dir.rmdir()

        with self.assertRaisesRegex(ReworkError, "receipt directory is missing"):
            prepare_rework(self.workspace, self.profile, setup["action_ref"])

    def test_missing_source_receipt_is_rejected(self) -> None:
        setup = self._reach_review("note")
        source_run = setup["source"]["run_id"]
        receipt_dir = self.state / "clockwork/run-receipts" / source_run
        lineage_ref = setup["final"]["lineage"]["artifact_ref"]
        removed = False
        for path in receipt_dir.iterdir():
            receipt = json.loads(path.read_text(encoding="utf-8"))
            if any(item["path"] == lineage_ref for item in receipt["writes"]):
                path.unlink()
                removed = True
                break
        self.assertTrue(removed)

        with self.assertRaisesRegex(ReworkError, "no completed Root Writer receipt"):
            prepare_rework(self.workspace, self.profile, setup["action_ref"])

    def test_traversal_and_symlink_action_refs_are_rejected(self) -> None:
        setup = self._reach_review("note")
        with self.assertRaises(ReworkError):
            prepare_rework(
                self.workspace,
                self.profile,
                f"../example/state/{setup['action_ref']}",
            )

        action_path = self.state / setup["action_ref"]
        outside = Path(self.temporary.name) / "outside-action.json"
        outside.write_bytes(action_path.read_bytes())
        action_path.unlink()
        action_path.symlink_to(outside)
        with self.assertRaises(ReworkError):
            prepare_rework(self.workspace, self.profile, setup["action_ref"])

    def test_existing_followup_action_is_rejected(self) -> None:
        setup = self._reach_review("note")
        followup = (
            self.state
            / "outbox/review-actions"
            / setup["review"]["artifact_id"]
            / "r2.json"
        )
        followup.write_text("{}\n", encoding="utf-8")

        with self.assertRaisesRegex(ReworkError, "Precondition failed; path must be absent"):
            prepare_rework(self.workspace, self.profile, setup["action_ref"])

    def test_followup_race_is_blocked_by_atomic_writer_precondition(self) -> None:
        setup = self._reach_review("note")
        relative = (
            f"outbox/review-actions/{setup['review']['artifact_id']}/r2.json"
        )
        followup = self.state / relative
        files_before = {
            path.relative_to(self.state).as_posix()
            for path in self.state.rglob("*")
            if path.is_file()
        }
        original = root_writer.validate_request

        def followup_appears(request, profile, workspace, **kwargs):
            prepared = original(request, profile, workspace, **kwargs)
            followup.parent.mkdir(parents=True, exist_ok=True)
            followup.write_text("{}\n", encoding="utf-8")
            return prepared

        with patch.object(root_writer, "validate_request", followup_appears):
            with self.assertRaisesRegex(
                ReworkError, "Precondition failed; path must be absent"
            ):
                prepare_rework(self.workspace, self.profile, setup["action_ref"])

        files_after = {
            path.relative_to(self.state).as_posix()
            for path in self.state.rglob("*")
            if path.is_file()
        }
        self.assertEqual(files_after - files_before, {relative})


if __name__ == "__main__":
    unittest.main()
