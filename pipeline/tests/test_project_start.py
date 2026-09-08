from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from pipeline import context_resolver
from pipeline.project_start import (
    ProjectStartError,
    ProjectStartService,
    append_readiness_snapshot,
    build_project_start_read_model,
    validate_start_brief,
)


SOURCE_WORKSPACE = Path(__file__).resolve().parents[2]
SOURCE_EXAMPLE = SOURCE_WORKSPACE / "projects" / "example"


class ProjectStartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name) / "workspace"
        self.workspace.mkdir()
        (self.workspace / "AGENTS.md").write_text("# Test\n", encoding="utf-8")
        self.profile_root = self.workspace / "projects" / "example"
        self.profile_root.parent.mkdir()
        shutil.copytree(
            SOURCE_EXAMPLE, self.profile_root, ignore=shutil.ignore_patterns("state")
        )
        self.profile = self.profile_root / "project.json"
        self.state = self.profile_root / "state"
        # Explicit legacy synthetic fixture: never import the operator's local state.
        brief = self._operator_payload()
        for field in ("baseline", "horizon", "resources", "do_nothing_option"):
            brief.pop(field, None)
        brief.update(
            brief_id="START-example-growth", brief_revision="r1", brief_version="1.0",
            created_by="operator", created_at="2026-08-31T17:00:00Z",
            contains_personal_data=False,
            authority={"human_review_required": True, "may_publish": False,
                       "may_change_public_accounts": False},
        )
        target = self.state / "records/project-start/briefs/START-example-growth/r1.json"
        target.parent.mkdir(parents=True)
        target.write_bytes(self._content(brief))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _content(value: object) -> bytes:
        return (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")

    def _operator_payload(self) -> dict[str, object]:
        return {
            "project_id": "example-project",
            "project_profile_revision": "example-project-profile-v2",
            "goal": {
                "title": "Clarify an initial project route",
                "detail": "Establish a bounded first recommendation from explicit operator inputs and verified research.",
            },
            "audience": {
                "label": "Independent product operators",
                "detail": "People responsible for turning a small product question into an evidence-backed next step.",
            },
            "success_signal": {
                "label": "One ready-to-queue recommendation",
                "detail": "The desk shows five explicit readiness steps and does not call the result a publication decision.",
            },
            "baseline": {
                "status": "measured",
                "detail": "A synthetic control baseline is measured only to exercise the deterministic project-start contract.",
            },
            "horizon": {
                "label": "One test cycle",
                "detail": "Use one deterministic test cycle to assess whether the project-start route can be queued.",
            },
            "resources": {
                "detail": "Use the local synthetic fixture, the named project roles, and no external accounts or personal data.",
            },
            "do_nothing_option": {
                "detail": "Leave the synthetic project unqueued; that makes no external change and remains a valid test outcome.",
            },
            "non_goals": [
                "Publish content or change a public account.",
                "Treat a synthetic example as a market result.",
            ],
            "research_question": "What is the smallest credible next recommendation for this project?",
        }

    def _not_measured_operator_payload(self) -> dict[str, object]:
        payload = self._operator_payload()
        payload["baseline"] = {
            "status": "not_measured",
            "detail": "No impact baseline exists in this synthetic test; analytics work remains explicitly open.",
        }
        return payload

    def _brief_pointer(self, brief_id: str = "START-example-growth") -> dict[str, str]:
        path = self.state / "records/project-start/briefs" / brief_id / "r1.json"
        return {
            "artifact_ref": f"records/project-start/briefs/{brief_id}/r1.json",
            "artifact_revision": "r1",
            "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    def _evidence_pointer(self) -> dict[str, str]:
        path = self.state / "evidence/packets/project-start-evidence-r1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = self._content(
            {
                "fixture": True,
                "statement": "A bounded synthetic evidence package supports a project-start readiness test only.",
            }
        )
        path.write_bytes(content)
        return {
            "artifact_ref": "evidence/packets/project-start-evidence-r1.json",
            "artifact_revision": "fixture-r1",
            "artifact_sha256": hashlib.sha256(content).hexdigest(),
        }

    def _ready_snapshot(self, brief_id: str = "START-example-growth") -> dict[str, object]:
        evidence = self._evidence_pointer()
        return {
            "project_id": "example-project",
            "project_profile_revision": "example-project-profile-v2",
            "readiness_version": "1.0",
            "snapshot_id": "START-READY-ready-to-queue",
            "brief": self._brief_pointer(brief_id),
            "audience": {
                "status": "validated",
                "detail": "The bounded fixture contains a validated audience signal for contract testing only.",
                "evidence_refs": [evidence],
            },
            "evidence": {
                "status": "sufficient",
                "detail": "The bounded fixture provides sufficient input for a synthetic first recommendation only.",
                "evidence_refs": [evidence],
            },
            "first_recommendation": {
                "status": "ready",
                "detail": "Queue one synthetic research-evidence route; this is not approval or publication.",
                "evidence_refs": [evidence],
            },
            "authority": {
                "may_publish": False,
                "may_change_public_accounts": False,
                "human_review_required": True,
            },
            "recorded_by": "growth-coordinator",
            "contains_personal_data": False,
            "recorded_at": "2026-08-31T18:00:00Z",
        }

    def test_empty_project_desk_has_a_clear_operator_start_state(self) -> None:
        shutil.rmtree(self.state / "records/project-start")

        desk = ProjectStartService(self.workspace).project_desk("example-project")

        self.assertEqual(desk["phase"], "brief_required")
        self.assertEqual(desk["readiness"]["completed"], 0)
        self.assertEqual(desk["readiness"]["total"], 5)
        self.assertEqual(desk["next_roles"][0]["role"], "operator")
        self.assertFalse(desk["authority"]["automatic_publish"])
        rendered = json.dumps(desk, sort_keys=True)
        self.assertNotIn("artifact_ref", rendered)
        self.assertNotIn("sha256", rendered)
        self.assertNotIn("records/", rendered)

    def test_runtime_publish_mode_replaces_the_hard_coded_desk_value(self) -> None:
        runtime = self.workspace / "runtime"
        runtime.mkdir()
        (runtime / "permissions.json").write_text(
            json.dumps({"publish": "automatic"}), encoding="utf-8"
        )

        desk = ProjectStartService(self.workspace).project_desk("example-project")

        self.assertEqual(desk["authority"]["publish_mode"], "automatic")
        self.assertEqual(desk["authority"]["publish_configuration"], "configured")
        self.assertTrue(desk["authority"]["automatic_publish"])
        self.assertFalse(desk["authority"]["human_review_required"])
        self.assertTrue(desk["authority"]["ready_to_queue_is_not_go_live"])

    def test_invalid_runtime_publish_mode_fails_closed_and_is_visible(self) -> None:
        runtime = self.workspace / "runtime"
        runtime.mkdir()
        (runtime / "permissions.json").write_text(
            json.dumps({"publish": "surprise"}), encoding="utf-8"
        )

        desk = ProjectStartService(self.workspace).project_desk("example-project")

        self.assertEqual(desk["authority"]["publish_mode"], "off")
        self.assertEqual(
            desk["authority"]["publish_configuration"], "invalid_fail_closed"
        )
        self.assertFalse(desk["authority"]["automatic_publish"])
        self.assertTrue(desk["authority"]["human_review_required"])

    def test_service_creates_operator_brief_with_server_owned_identity(self) -> None:
        shutil.rmtree(self.state / "records/project-start")
        service = ProjectStartService(self.workspace)

        result = service.create_start_brief(self._operator_payload())

        self.assertTrue(result["created"])
        self.assertRegex(result["brief_id"], r"^START-[0-9a-f]{24}$")
        self.assertEqual(result["brief_revision"], "r1")
        self.assertEqual(result["desk"]["phase"], "audience_research")
        stored = sorted((self.state / "records/project-start/briefs").rglob("*.json"))
        self.assertEqual(len(stored), 1)
        value = json.loads(stored[0].read_text(encoding="utf-8"))
        self.assertEqual(value["created_by"], "operator")
        self.assertFalse(value["authority"]["may_publish"])
        self.assertTrue(value["authority"]["human_review_required"])
        self.assertEqual(value["brief_version"], "2.0")
        self.assertEqual(value["baseline"]["status"], "measured")
        self.assertEqual(value["horizon"], self._operator_payload()["horizon"])
        self.assertEqual(value["resources"], self._operator_payload()["resources"])
        self.assertEqual(
            value["do_nothing_option"], self._operator_payload()["do_nothing_option"]
        )

    def test_new_work_item_does_not_replace_the_project_direction(self) -> None:
        service = ProjectStartService(self.workspace)
        direction = service.project_desk("example-project")["goal"]
        payload = self._operator_payload()
        payload["goal"] = {
            "title": "Correct one exact sentence",
            "detail": "Prepare one bounded correction without replacing the longer-lived project direction.",
        }

        created = service.create_start_brief(payload)
        desk = service.project_desk("example-project")

        self.assertTrue(created["created"])
        self.assertEqual(desk["goal"], direction)
        self.assertEqual(desk["current_work"], payload["goal"])

    def test_not_measured_baseline_is_an_analytics_blocker_not_a_research_gate(self) -> None:
        desk = ProjectStartService(self.workspace).project_desk("example-project")

        self.assertEqual(desk["phase"], "audience_research")
        self.assertEqual(desk["baseline"]["status"], "not_measured")
        self.assertIn(
            {
                "title": "Baseline needed",
                "detail": "This existing project brief does not record a baseline. Analytics must add one before impact or experiment claims are made.",
                "owner_role": "analytics-experimentation",
            },
            desk["blockers"],
        )
        steps = {step["id"]: step for step in desk["readiness"]["steps"]}
        self.assertEqual(steps["baseline"]["status"], "blocked")
        self.assertEqual(steps["audience"]["status"], "active")
        roles = {role["role"]: role["status"] for role in desk["next_roles"]}
        self.assertEqual(roles["analytics-experimentation"], "active")
        self.assertEqual(roles["audience-voc"], "active")

    def test_safe_discovery_context_does_not_block_audience_research(self) -> None:
        profile = json.loads(self.profile.read_text(encoding="utf-8"))
        profile["product_motion"] = "product-led-software"
        self.profile.write_bytes(self._content(profile))

        with patch(
            "pipeline.project_start.context_resolver.resolve_project_context"
        ) as resolve:
            resolve.side_effect = [
                context_resolver.ContextResolutionError("full context is incomplete"),
                object(),
            ]
            desk = ProjectStartService(self.workspace).project_desk("example-project")

        self.assertEqual(desk["phase"], "audience_research")
        steps = {step["id"]: step for step in desk["readiness"]["steps"]}
        self.assertEqual(steps["context"]["status"], "complete")
        self.assertEqual(steps["audience"]["status"], "active")
        self.assertIn("Safe discovery context", steps["context"]["detail"])
        self.assertEqual(resolve.call_args_list[0].kwargs["run_mode"], "live")
        self.assertEqual(resolve.call_args_list[1].kwargs["run_mode"], "discovery")

    def test_research_recommendation_can_be_ready_without_an_invented_baseline(self) -> None:
        service = ProjectStartService(self.workspace)
        created = service.create_start_brief(self._not_measured_operator_payload())
        snapshot = self._ready_snapshot(created["brief_id"])
        append_readiness_snapshot(
            self.workspace,
            self.profile,
            snapshot,
            run_id="RUN-project-start-not-measured-001",
            idempotency_key="34061406-7c36-42c8-a1a9-129271e21b9a",
        )

        desk = build_project_start_read_model(
            self.workspace, self.profile, brief_id=created["brief_id"]
        )

        self.assertEqual(desk["phase"], "research_recommendation_ready")
        self.assertEqual(desk["readiness"]["completed"], 4)
        self.assertEqual(desk["readiness"]["total"], 5)
        self.assertEqual(desk["readiness"]["steps"][0]["status"], "blocked")
        self.assertIn("no impact, experiment, or launch claim", desk["active_research"]["detail"])
        self.assertNotEqual(desk["phase"], "ready_to_queue")

    def test_stale_profile_and_malformed_brief_are_safe_public_errors(self) -> None:
        service = ProjectStartService(self.workspace)
        stale = self._operator_payload()
        stale["project_profile_revision"] = "example-project-profile-v1"
        with self.assertRaises(ProjectStartError) as stale_error:
            service.create_start_brief(stale)
        self.assertEqual(stale_error.exception.status, 409)
        self.assertEqual(stale_error.exception.code, "stale_project_profile")

        invalid = self._operator_payload()
        invalid["non_goals"] = []
        with self.assertRaises(ProjectStartError) as invalid_error:
            service.create_start_brief(invalid)
        self.assertEqual(invalid_error.exception.status, 400)
        self.assertEqual(invalid_error.exception.code, "invalid_project_brief")

    def test_evidence_bound_snapshot_reaches_ready_to_queue_not_go_live(self) -> None:
        service = ProjectStartService(self.workspace)
        created = service.create_start_brief(self._operator_payload())
        snapshot = self._ready_snapshot(created["brief_id"])
        receipt = append_readiness_snapshot(
            self.workspace,
            self.profile,
            snapshot,
            run_id="RUN-project-start-ready-001",
            idempotency_key="9095ad1d-d885-4245-b09a-821523fa3251",
        )

        desk = build_project_start_read_model(
            self.workspace, self.profile, brief_id=created["brief_id"]
        )

        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(desk["phase"], "ready_to_queue")
        self.assertEqual(desk["readiness"]["completed"], 5)
        self.assertEqual(
            [step["status"] for step in desk["readiness"]["steps"]],
            ["complete", "complete", "complete", "complete", "complete"],
        )
        self.assertIn("not approved for publication", desk["active_research"]["detail"])
        self.assertFalse(desk["authority"]["automatic_publish"])
        self.assertTrue(desk["authority"]["human_review_required"])

    def test_ready_recommendation_without_exact_evidence_fails_closed(self) -> None:
        created = ProjectStartService(self.workspace).create_start_brief(
            self._operator_payload()
        )
        snapshot = self._ready_snapshot(created["brief_id"])
        snapshot["audience"] = {
            "status": "validated",
            "detail": "The fixture incorrectly claims validation without evidence references.",
            "evidence_refs": [],
        }

        with self.assertRaisesRegex(ProjectStartError, "project-start-readiness@1"):
            append_readiness_snapshot(
                self.workspace,
                self.profile,
                snapshot,
                run_id="RUN-project-start-invalid-001",
                idempotency_key="de2c26e7-bebe-4d04-aa6c-002dc6ddd8e4",
            )

    def test_brief_cannot_grant_publication_authority(self) -> None:
        stored = json.loads(
            (self.state / "records/project-start/briefs/START-example-growth/r1.json").read_text(
                encoding="utf-8"
            )
        )
        invalid = copy.deepcopy(stored)
        invalid["authority"]["may_publish"] = True

        with self.assertRaisesRegex(ProjectStartError, "project-start-brief@1"):
            validate_start_brief(self.profile, invalid)


if __name__ == "__main__":
    unittest.main()
