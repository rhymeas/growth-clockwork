from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import unittest

from pipeline import run_engine
from pipeline.tests import test_run_engine
from pipeline.workhorse import WorkhorseError, _read_bundle, prepare_submission, submit_bundle


class WorkhorseTests(unittest.TestCase):
    """Exercise authored packaging against an isolated, receipted engine run."""

    def setUp(self) -> None:
        self.harness = test_run_engine.RunEngineTests()
        self.harness.setUp()
        self.addCleanup(self.harness.tearDown)
        self.workspace = self.harness.workspace
        self.profile = self.harness.profile
        self.state = self.harness.state
        plan = self.harness._plan("EX-982", "research-evidence")
        plan["operating_contract"]["resource_limits"]["usage_policy"] = "codex_subscription"
        prepared = self.harness._prepare_plan(plan)
        self.manifest = self.harness._manifest(prepared["manifest"])
        self.entry = self.manifest["tasks"][0]
        self.task_ref = self.entry["task_ref"]
        self.task = json.loads((self.state / self.task_ref).read_text())
        self.evidence = copy.deepcopy(self.task["input_artifacts"][:1])

    def bundle(self, *, support: bool = False) -> dict:
        evidence = copy.deepcopy(self.evidence)
        supports = []
        if support:
            supports = [{
                "local_id": "notes",
                "artifact_ref": "records/decisions/workhorse-source-r1.md",
                "artifact_revision": "r1",
                "media_type": "text/markdown",
                "content": "# Review notes\n\nA café example — explicitly synthetic.\r\n",
            }]
            evidence.append({"local_id": "notes"})
        return {
            "bundle_version": "1.0",
            "task_ref": self.task_ref,
            "status": "completed",
            "supporting_artifacts": supports,
            "outputs": [{
                "local_id": "coordination",
                "artifact_ref": "records/decisions/workhorse-coordinate-r1.json",
                "artifact_revision": "r1",
                "contract_id": "task-set@1",
                "payload": {
                    "outcome_owner": "growth-coordinator",
                    "decision_question": "Can this isolated route preserve an authored result?",
                    "audience": {"status": "synthetic test"},
                    "business_goal": {"objective": "Exercise the result boundary"},
                    "baseline": {"status": "not_measured"},
                    "horizon": {"scope": "one test run"},
                    "success_signal": {"name": "exact authored bytes retained"},
                    "guardrails": ["Do not publish"],
                    "non_goals": ["No real audience claims"],
                    "do_nothing_option": {"action": "leave state unchanged"},
                    "stop_condition": {"rule": "stop after one accepted test task"},
                    "resource_limits": {"external_cost_eur": 0},
                    "tasks": [{"role": "evidence-research", "action": "inspect test evidence"}],
                },
                "evidence_refs": copy.deepcopy(evidence),
                "unknowns": ["Real audience demand is unmeasured."],
                "assumptions": ["This payload exercises packaging only."],
                "confidence": "low",
                "quality_checks": [
                    {"check_id": check, "status": "pass", "evidence_refs": copy.deepcopy(evidence)}
                    for check in (
                        "project_binding", "single_outcome_owner", "objective_qualified",
                        "route_authority", "bounded_scope", "stop_rule",
                    )
                ],
            }],
            "evidence_refs": evidence,
            "checks": [{"name": "authored review", "result": "pass", "evidence_ref": self.evidence[0]}],
            "attempts": 1,
            "usage": {"duration_ms": 13, "input_tokens": None, "output_tokens": None, "variable_external_cost_eur": 0},
            "provenance": {"prompt_version": "workhorse-boundary-test-v1"},
            "created_at": "2026-09-01T08:05:00Z",
            "error": None,
        }

    def snapshot(self) -> dict[str, bytes]:
        return {str(path.relative_to(self.state)): path.read_bytes() for path in self.state.rglob("*") if path.is_file()}

    def submit(self, bundle: dict) -> dict:
        return submit_bundle(self.workspace, self.profile, self.task_ref, self.harness._temporary_json(bundle))

    def test_prepare_preserves_authored_content_and_unknown_usage_without_state_writes(self) -> None:
        bundle = self.bundle()
        before = self.snapshot()
        submission = prepare_submission(self.workspace, self.profile, self.task_ref, bundle)
        result = submission["result"]
        self.assertIsNone(result["usage"]["input_tokens"])
        self.assertIsNone(result["usage"]["output_tokens"])
        self.assertEqual(result["usage"]["duration_ms"], 13)
        self.assertEqual(result["provenance"]["provider"], "codex")
        self.assertEqual(result["provenance"]["model"], "not_exposed")
        content = submission["artifacts"][0]["content"]
        value = json.loads(content)
        self.assertEqual(value["payload"], bundle["outputs"][0]["payload"])
        self.assertEqual(value["quality_checks"], bundle["outputs"][0]["quality_checks"])
        self.assertEqual(value["unknowns"], bundle["outputs"][0]["unknowns"])
        self.assertEqual(value["task_sha256"], self.entry["task_sha256"])
        self.assertEqual(hashlib.sha256(content.encode()).hexdigest(), result["output_artifacts"][0]["artifact_sha256"])
        self.assertEqual(self.snapshot(), before)

    def test_submit_support_exact_bytes_advances_and_replays_without_duplicate_writes(self) -> None:
        bundle = self.bundle(support=True)
        result = self.submit(bundle)
        self.assertEqual(result["run_status"], "running")
        support = bundle["supporting_artifacts"][0]
        self.assertEqual((self.state / support["artifact_ref"]).read_bytes(), support["content"].encode("utf-8"))
        output = json.loads((self.state / bundle["outputs"][0]["artifact_ref"]).read_text())
        self.assertEqual(output["evidence_refs"][1]["artifact_sha256"], hashlib.sha256(support["content"].encode()).hexdigest())
        after = self.snapshot()
        replay = self.submit(bundle)
        self.assertEqual(replay["run_status"], "running")
        self.assertEqual(self.snapshot(), after)

    def test_missing_authored_fields_and_unmeasured_duration_rejected(self) -> None:
        before = self.snapshot()
        for field in ("quality_checks", "unknowns", "confidence", "assumptions", "payload"):
            with self.subTest(field=field):
                bundle = self.bundle()
                del bundle["outputs"][0][field]
                with self.assertRaises(WorkhorseError):
                    prepare_submission(self.workspace, self.profile, self.task_ref, bundle)
        bundle = self.bundle()
        bundle["usage"]["duration_ms"] = None
        with self.assertRaises(run_engine.RunEngineError):
            self.submit(bundle)
        self.assertEqual(self.snapshot(), before)

    def test_authored_semantic_failure_is_not_rewritten_to_pass(self) -> None:
        before = self.snapshot()
        bundle = self.bundle()
        bundle["outputs"][0]["quality_checks"][0]["status"] = "fail"
        submission = prepare_submission(self.workspace, self.profile, self.task_ref, bundle)
        self.assertEqual(json.loads(submission["artifacts"][0]["content"])["quality_checks"][0]["status"], "fail")
        with self.assertRaisesRegex(run_engine.RunEngineError, "not pass"):
            self.submit(bundle)
        self.assertEqual(self.snapshot(), before)

    def test_undeclared_or_wrong_root_support_is_rejected_by_engine(self) -> None:
        before = self.snapshot()
        bundle = self.bundle(support=True)
        bundle["supporting_artifacts"][0]["artifact_ref"] = "content/unowned-evidence.md"
        with self.assertRaisesRegex(run_engine.RunEngineError, "outside task write roots"):
            self.submit(bundle)
        bundle = self.bundle(support=True)
        bundle["evidence_refs"] = self.evidence
        bundle["outputs"][0]["evidence_refs"] = self.evidence
        with self.assertRaises(run_engine.RunEngineError):
            self.submit(bundle)
        self.assertEqual(self.snapshot(), before)

    def test_task_mismatch_and_unresolved_pointer_rejected(self) -> None:
        bundle = self.bundle()
        bundle["task_ref"] = "handoffs/another-run/task.json"
        with self.assertRaisesRegex(WorkhorseError, "task_ref differs"):
            prepare_submission(self.workspace, self.profile, self.task_ref, bundle)
        bundle = self.bundle()
        bundle["evidence_refs"] = [{"local_id": "not-authored"}]
        with self.assertRaisesRegex(WorkhorseError, "unresolved"):
            prepare_submission(self.workspace, self.profile, self.task_ref, bundle)

    def test_review_candidate_preserves_prior_output_pointer(self) -> None:
        bundle = self.bundle()
        bundle["review_candidate"] = {
            "artifact": self.evidence[0],
            "artifact_id": "test-product",
            "title": "Readable result",
            "type": "article",
            "preview": "A complete readable result.",
            "evidence": [],
            "quality_checks": [],
        }
        submission = prepare_submission(self.workspace, self.profile, self.task_ref, bundle)
        for field, value in self.evidence[0].items():
            self.assertEqual(submission["review_candidate"][field], value)
        before = self.snapshot()
        with self.assertRaisesRegex(run_engine.RunEngineError, "only allowed for completed final governance"):
            self.submit(bundle)
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_json_keys_rejected_before_packaging(self) -> None:
        path = Path(self.harness.temporary.name) / "duplicate.json"
        path.write_text('{"bundle_version":"1.0","bundle_version":"2.0"}')
        with self.assertRaisesRegex(WorkhorseError, "duplicate"):
            _read_bundle(path)


if __name__ == "__main__":
    unittest.main()
