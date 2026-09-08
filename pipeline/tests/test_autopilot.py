from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import unittest
import uuid

from pipeline import autopilot, project_start
from pipeline.coordinator import prepare_run
from pipeline.review_api import ReviewService
from pipeline.project_memory import append_memory_record
from pipeline.tests import test_rework as rework_fixtures
from pipeline.tests.draft_project_fixture import create_draft_project


class AutopilotTests(unittest.TestCase):
    """Exercise real manifests, receipts, review actions and replay in isolation."""

    def setUp(self) -> None:
        self.fixture = rework_fixtures.ReworkTests()
        self.fixture.setUp()
        self.workspace = self.fixture.workspace
        self.profile = self.fixture.profile
        self.state = self.fixture.state
        # Only the freshly copied test state is removed; source artifacts survive.
        shutil.rmtree(self.state, ignore_errors=True)
        self.brief = json.loads((rework_fixtures.SOURCE_WORKSPACE / "projects/example/fixtures/schema-contracts/project-start/01-brief-valid.json").read_text())

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def _save_brief(self, *, content: bool = False) -> None:
        if content:
            self.brief["goal"]["title"] = "Create a useful educational article"
            self.brief["success_signal"]["detail"] = "A complete source-backed lesson is ready for exact final review."
        project_start.append_start_brief(self.workspace, self.profile, self.brief, run_id="START-test-brief")

    def _state_hashes(self) -> dict[str, str]:
        if not self.state.exists():
            return {}
        return {path.relative_to(self.state).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in self.state.rglob("*") if path.is_file()}

    def _accept(self, submission: dict, *, native: bool) -> dict:
        if native:
            # Simulate Codex's unexposed counters inside an explicit fixture run.
            submission["result"]["provenance"].update(provider="codex", model="synthetic-codex-worker")
            submission["result"]["usage"].update(input_tokens=None, output_tokens=None)
        return self.fixture._accept(submission)

    def _submission(self, *args, revision: str = "r1", **kwargs) -> dict:
        submission = self.fixture._submission(*args, **kwargs)
        if revision != "r1":
            artifact = submission["artifacts"][0]
            content = json.loads(artifact["content"])
            content["artifact_revision"] = revision
            artifact["content"] = json.dumps(content, ensure_ascii=False, indent=2) + "\n"
            artifact["artifact_revision"] = revision
            artifact["artifact_sha256"] = hashlib.sha256(artifact["content"].encode()).hexdigest()
            submission["result"]["output_artifacts"][0].update(artifact_revision=revision, artifact_sha256=artifact["artifact_sha256"])
            submission["result"]["provenance"]["output_revision"] = revision
        return submission

    def _reach_review(self, *, action: str | None = None, use_autopilot: bool = False, revision: str = "r1", artifact_id: str | None = None) -> dict:
        if use_autopilot:
            run = autopilot.advance_autopilot(self.workspace, self.profile)
            manifest = next((self.state / "records/runs" / run["run_id"]).glob("manifest-*.json"))
            first = json.loads(manifest.read_text())
        else:
            prepared = prepare_run(self.workspace, self.profile, self.fixture._temporary_json(self.fixture._plan()))
            first = self.fixture._manifest(prepared["manifest"])
        stem = first["run_id"]
        result = self._accept(self._submission(first, "coordinate", "task-set@1", f"records/decisions/{stem}-coordinate.json", revision=revision), native=use_autopilot)
        second = self.fixture._manifest(result["manifest"])
        producer_ref = f"evidence/packets/{stem}-evidence.json"
        submission = self._submission(second, "research", "evidence-packet@1", producer_ref, revision=revision)
        pointer = submission["result"]["output_artifacts"][0]
        result = self._accept(submission, native=use_autopilot)
        third = self.fixture._manifest(result["manifest"])
        candidate = {
            "artifact_ref": producer_ref, "artifact_revision": revision, "artifact_sha256": pointer["artifact_sha256"],
            "artifact_id": artifact_id or f"{stem}-final", "title": "Synthetic bounded evidence", "type": "evidence-packet",
            "preview": "A synthetic fixture final product.", "evidence": [{"label": "Producer", "ref": producer_ref}],
            "quality_checks": [{"name": "Fixture checks", "status": "pass", "detail": "Synthetic local checks only."}],
        }
        result = self._accept(self._submission(third, "governance-final", "governance-verdict@1", f"records/governance/{stem}-final.json", review_candidate=candidate, revision=revision), native=use_autopilot)
        review = json.loads((self.state / result["review_item"]["artifact_ref"]).read_text())
        if action:
            request = {key: review[key] for key in ("project_id", "project_profile_revision", "artifact_id", "artifact_revision", "artifact_sha256")}
            request["action"] = action
            if action == "note":
                request["note"] = "Make the uncertainty explicit in the opening paragraph."
            elif action == "decline":
                request["reason"] = "Do not continue this direction."
            service = ReviewService(self.workspace)
            try:
                service.review_action(request)
            finally:
                service.close()
        return review

    def test_inspection_without_brief_is_read_only(self) -> None:
        before = self._state_hashes()
        view = autopilot.inspect_autopilot(self.workspace, self.profile)
        self.assertEqual(view["action"], "brief_required")
        self.assertEqual(before, self._state_hashes())

    def test_content_goal_prepares_actual_tasks_and_replays_identically(self) -> None:
        self._save_brief(content=True)
        before = self._state_hashes()
        preview = autopilot.inspect_autopilot(self.workspace, self.profile)
        self.assertEqual(preview["route_id"], "content-channel")
        self.assertEqual(before, self._state_hashes())
        first = autopilot.advance_autopilot(self.workspace, self.profile)
        self.assertEqual(first["action"], "dispatch")
        self.assertEqual(first["route_id"], "content-channel")
        order = first["work_orders"][0]
        self.assertEqual(order["task"]["assigned_role"], "growth-coordinator")
        self.assertIn("Create a useful educational article", order["task"]["objective"])
        self.assertEqual(order["deliverable_contract"]["contract_id"], "task-set@1")
        self.assertTrue(order["verified_inputs"])
        self.assertIsNone(order["submission_template"]["result"]["usage"]["input_tokens"])
        self.assertEqual(order["submission_template"]["result"]["provenance"]["provider"], "codex")
        after_first = self._state_hashes()
        second = autopilot.advance_autopilot(self.workspace, self.profile, route_id="research-evidence")
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(first["work_orders"], second["work_orders"])
        self.assertEqual(after_first, self._state_hashes())

    def test_pending_review_does_not_start_duplicate_work(self) -> None:
        self._save_brief()
        self._reach_review(use_autopilot=True)
        unrelated = self.fixture._plan()
        unrelated["work_item_id"] = "EX-778"
        prepare_run(self.workspace, self.profile, self.fixture._temporary_json(unrelated))
        before = self._state_hashes()
        self.assertEqual(autopilot.advance_autopilot(self.workspace, self.profile)["action"], "awaiting_review")
        self.assertEqual(before, self._state_hashes())

    def test_notes_take_precedence_and_rework_is_consumed_once(self) -> None:
        self._save_brief()
        review = self._reach_review(action="note", use_autopilot=True)
        # An unrelated same-project queued run must not displace the exact note.
        plan = self.fixture._plan()
        plan["request_id"] = str(uuid.uuid4())
        plan["work_item_id"] = "EX-777"
        prepare_run(self.workspace, self.profile, self.fixture._temporary_json(plan))
        inspected = autopilot.inspect_autopilot(self.workspace, self.profile)
        self.assertEqual(inspected["action"], "prepare_rework")
        self.assertIn(review["artifact_id"], inspected["action_ref"])
        first = autopilot.advance_autopilot(self.workspace, self.profile)
        self.assertEqual(first["action"], "dispatch")
        self.assertIn("Make the uncertainty explicit", first["work_orders"][0]["task"]["objective"])
        before = self._state_hashes()
        second = autopilot.advance_autopilot(self.workspace, self.profile)
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(before, self._state_hashes())

    def test_terminal_reviews_settle_brief_and_new_brief_starts_new_cycle(self) -> None:
        self._save_brief()
        self._reach_review(action="decline", use_autopilot=True)
        before = self._state_hashes()
        self.assertEqual(autopilot.advance_autopilot(self.workspace, self.profile)["action"], "settled")
        self.assertEqual(before, self._state_hashes())
        self.brief["brief_revision"] = "r2"
        self.brief["created_at"] = "2026-09-04T00:00:00Z"
        self._save_brief()
        self.assertEqual(autopilot.advance_autopilot(self.workspace, self.profile)["action"], "dispatch")

    def test_new_brief_can_start_after_prior_run_is_blocked(self) -> None:
        self._save_brief()
        prepared = autopilot.advance_autopilot(self.workspace, self.profile)
        manifest_path = max(
            (self.state / "records/runs" / prepared["run_id"]).glob("manifest-*.json")
        )
        manifest = json.loads(manifest_path.read_text())
        submission = self._submission(
            manifest,
            "coordinate",
            "task-set@1",
            f"records/decisions/{prepared['run_id']}-coordinate.json",
        )
        submission["result"].update(
            status="needs_attention",
            output_artifacts=[],
            error={"class": "governance", "message": "Exact revision required.", "retryable": False},
        )
        submission["result"]["provenance"]["output_revision"] = None
        submission["artifacts"] = []
        self._accept(submission, native=True)

        self.brief["brief_revision"] = "r2"
        self.brief["created_at"] = "2026-09-04T00:00:00Z"
        self.brief["goal"]["detail"] = "Correct the blocked draft in a new immutable cycle."
        self._save_brief()

        view = autopilot.advance_autopilot(self.workspace, self.profile)
        self.assertEqual(view["action"], "dispatch")
        self.assertNotEqual(view["run_id"], prepared["run_id"])

    def test_approval_does_not_publish_or_regenerate(self) -> None:
        self._save_brief()
        self._reach_review(action="approve", use_autopilot=True)
        before = self._state_hashes()
        self.assertEqual(autopilot.advance_autopilot(self.workspace, self.profile)["action"], "settled")
        self.assertEqual(before, self._state_hashes())

    def test_missing_selected_brief_cannot_fall_back(self) -> None:
        self._save_brief()
        with self.assertRaisesRegex(autopilot.AutopilotError, "does not exist"):
            autopilot.advance_autopilot(self.workspace, self.profile, brief_id="START-other-project")

    def test_second_project_does_not_resume_another_projects_run(self) -> None:
        self._save_brief()
        autopilot.advance_autopilot(self.workspace, self.profile)
        profile_path = create_draft_project(self.workspace)
        view = autopilot.inspect_autopilot(self.workspace, profile_path)
        self.assertEqual(view["project_id"], "research-example")
        self.assertEqual(view["action"], "brief_required")
        self.assertFalse(view["work_orders"])

    def test_discovery_from_draft_context_preserves_unapproved_facts(self) -> None:
        profile_path = create_draft_project(self.workspace)
        target = profile_path.parent
        profile = json.loads(profile_path.read_text())
        self.brief.update(project_id=profile["project_id"], project_profile_revision=profile["profile_revision"], brief_id="START-discovery-test")
        self.brief["goal"] = {"title": "Prepare an educational article", "detail": "Explain a useful research practice using public primary sources and no product claims."}
        project_start.append_start_brief(self.workspace, profile_path, self.brief, run_id="START-discovery-test")
        context_before = {path.relative_to(target).as_posix(): path.read_bytes() for path in (target / "context").rglob("*.json")}
        view = autopilot.advance_autopilot(self.workspace, profile_path)
        self.assertEqual(view["action"], "dispatch")
        self.assertEqual(view["route_id"], "content-channel")
        task = view["work_orders"][0]["task"]
        state = self.workspace / profile["writer"]["state_root"]
        context = json.loads((state / task["project_context"]["artifact_ref"]).read_text())
        self.assertEqual(context["status"], "draft")
        self.assertIn("unapproved", task["objective"])
        self.assertEqual(context_before, {path.relative_to(target).as_posix(): path.read_bytes() for path in (target / "context").rglob("*.json")})

    def test_explicit_route_must_be_enabled(self) -> None:
        self._save_brief()
        before = self._state_hashes()
        with self.assertRaisesRegex(autopilot.AutopilotError, "not enabled"):
            autopilot.advance_autopilot(self.workspace, self.profile, route_id="unregistered-route")
        self.assertEqual(before, self._state_hashes())

    def test_unreceipted_or_tampered_brief_cannot_start_work(self) -> None:
        self._save_brief()
        path = self.state / project_start.brief_relative(self.brief)
        self.brief["goal"]["detail"] = "A replacement objective was not recorded through Root Writer."
        self.fixture._write_json(path, self.brief)
        with self.assertRaisesRegex(ValueError, "receipt covers exact bytes"):
            autopilot.advance_autopilot(self.workspace, self.profile)

    def test_cross_project_brief_identity_cannot_start_work(self) -> None:
        self._save_brief()
        path = self.state / project_start.brief_relative(self.brief)
        self.brief["project_id"] = "another-project"
        self.fixture._write_json(path, self.brief)
        with self.assertRaises(ValueError):
            autopilot.advance_autopilot(self.workspace, self.profile)

    def test_modified_task_is_not_resumed(self) -> None:
        self._save_brief()
        view = autopilot.advance_autopilot(self.workspace, self.profile)
        task = view["work_orders"][0]
        path = self.state / task["task_pointer"]["artifact_ref"]
        value = json.loads(path.read_text())
        value["objective"] = "Tampered task must not execute."
        self.fixture._write_json(path, value)
        with self.assertRaises(ValueError):
            autopilot.inspect_autopilot(self.workspace, self.profile)

    def test_project_desk_exposes_compact_read_only_automation_progress(self) -> None:
        self._save_brief(content=True)
        autopilot.advance_autopilot(self.workspace, self.profile)
        before = self._state_hashes()
        service = ReviewService(self.workspace)
        try:
            desk = service.project_desk(self.brief["project_id"])
        finally:
            service.close()
        automation = desk["automation"]
        self.assertEqual(automation["action"], "dispatch")
        self.assertEqual(automation["route_id"], "content-channel")
        self.assertEqual(automation["active_roles"], [{"role_id": "growth-coordinator", "label": "Growth Coordinator", "step_id": "coordinate"}])
        self.assertEqual(automation["progress"]["completed"], 0)
        self.assertGreater(automation["progress"]["total"], 1)
        for technical in ("artifact_ref", "sha256", "work_orders", "submission_template", "records/", "payload"):
            self.assertNotIn(technical, json.dumps(automation))
        self.assertEqual(before, self._state_hashes())

    def test_rework_retains_exact_source_context_and_rejects_tampering(self) -> None:
        self._save_brief()
        self._reach_review(action="note", use_autopilot=True)
        view = autopilot.advance_autopilot(self.workspace, self.profile)
        pointer = view["work_orders"][0]["task"]["project_context"]
        self.assertNotIn(view["run_id"], pointer["artifact_ref"])
        source_context = self.state / pointer["artifact_ref"]
        original = source_context.read_bytes()
        source_context.write_bytes(original + b"\n")
        with self.assertRaisesRegex(ValueError, "hash"):
            autopilot.inspect_autopilot(self.workspace, self.profile)
        service = ReviewService(self.workspace)
        try:
            status = service.project_desk(self.brief["project_id"])["automation"]
        finally:
            service.close()
        self.assertEqual(status["action"], "blocked")
        self.assertNotIn("records/", json.dumps(status))

    def test_second_human_note_preserves_original_context_and_usage_contract(self) -> None:
        self._save_brief()
        original = self._reach_review(action="note", use_autopilot=True)
        first_rework = autopilot.advance_autopilot(self.workspace, self.profile)
        context = first_rework["work_orders"][0]["task"]["project_context"]
        self._reach_review(action="note", use_autopilot=True, revision="r2", artifact_id=original["artifact_id"])
        second_rework = autopilot.advance_autopilot(self.workspace, self.profile)
        self.assertNotEqual(first_rework["run_id"], second_rework["run_id"])
        self.assertEqual(second_rework["work_orders"][0]["task"]["project_context"], context)
        task = second_rework["work_orders"][0]["task"]
        plan_ref = next(pointer for pointer in task["input_artifacts"] if pointer["artifact_ref"].startswith("records/coordinator-plans/"))
        plan = json.loads((self.state / plan_ref["artifact_ref"]).read_text())
        self.assertEqual(plan["operating_contract"]["resource_limits"]["usage_policy"], "codex_subscription")
        self.assertEqual(plan["operating_contract"]["created_at"], plan["requested_at"])

    def test_nested_context_tampering_cannot_reach_a_workhorse(self) -> None:
        self._save_brief()
        view = autopilot.advance_autopilot(self.workspace, self.profile)
        section = view["work_orders"][0]["verified_context"]["sections"][0]
        path = self.state / section["artifact_ref"]
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "hash"):
            autopilot.inspect_autopilot(self.workspace, self.profile)

    def test_work_order_memory_is_bounded_project_bound_and_read_only(self) -> None:
        self._save_brief()
        initial = autopilot.advance_autopilot(self.workspace, self.profile)
        evidence = {**initial["brief"], "relation": "supports"}
        for index in range(7):
            exploratory = index == 6
            record = {
                "project_id": self.brief["project_id"],
                "project_profile_revision": self.brief["project_profile_revision"],
                "record_version": "1.0", "memory_id": f"MEM-process-{index:03d}",
                "memory_revision": "r1", "memory_type": "learning",
                "claim_mode": "hypothesis" if exploratory else "observation",
                "statement": "The saved project brief records an evidence question and a bounded useful outcome.",
                "tags": ["project", "evidence"], "evidence": [evidence],
                "evidence_grade": "exploratory" if exploratory else "verified",
                "decision_eligible": not exploratory, "causal_claim": False,
                "experiment_ref": None, "supersedes": [], "confidence": "low",
                "source_run_id": initial["run_id"],
                "source_task_id": initial["work_orders"][0]["task"]["task_id"],
                "created_by": "workhorse", "contains_personal_data": False,
                "valid_from": "2026-09-01T00:00:00Z", "expires_at": None,
                "created_at": "2026-09-01T00:00:00Z",
            }
            append_memory_record(self.workspace, self.profile, record, run_id=f"RUN-memory-test-{index:03d}")
        other_profile = create_draft_project(self.workspace)
        other_identity = json.loads(other_profile.read_text())
        other_brief = json.loads(json.dumps(self.brief))
        other_brief.update(project_id=other_identity["project_id"], project_profile_revision=other_identity["profile_revision"])
        project_start.append_start_brief(self.workspace, other_profile, other_brief, run_id="START-other-project")
        other_run = autopilot.advance_autopilot(self.workspace, other_profile)
        self.assertEqual(other_run["work_orders"][0]["project_memory"]["matched"], [])
        before = self._state_hashes()
        order = autopilot.inspect_autopilot(self.workspace, self.profile)["work_orders"][0]
        memory = order["project_memory"]
        self.assertEqual(memory["project_id"], self.brief["project_id"])
        self.assertEqual(memory["query"], order["task"]["objective"])
        self.assertFalse(memory["include_exploratory"])
        self.assertEqual(len(memory["matched"]), 5)
        self.assertNotIn("MEM-process-006", [item["memory_id"] for item in memory["matched"]])
        for matched in memory["matched"]:
            self.assertEqual(matched["record_sha256"], hashlib.sha256((self.state / matched["record_ref"]).read_bytes()).hexdigest())
        self.assertEqual(before, self._state_hashes())


if __name__ == "__main__":
    unittest.main()
