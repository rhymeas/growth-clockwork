from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid

from pipeline.coordinator import prepare_run
from pipeline.run_engine import accept_result
from pipeline.root_writer import apply_request
from pipeline.runtime_status import RuntimeStatusError, inspect_runtime, main
from pipeline.tests.professional_fixture import professional_deliverable_content
from pipeline.tests.operating_fixture import operating_contract


SOURCE_WORKSPACE = Path(__file__).resolve().parents[2]


class RuntimeStatusTests(unittest.TestCase):
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
            ignore=shutil.ignore_patterns("state"),
        )
        self.profile = self.workspace / "projects/example/project.json"
        self.state = self.workspace / "projects/example/state"
        shutil.rmtree(self.state, ignore_errors=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write_json(path: Path, value: object, *, sort_keys: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=sort_keys) + "\n",
            encoding="utf-8",
        )

    def _temporary_json(self, value: object) -> Path:
        path = Path(self.temporary.name) / f"input-{uuid.uuid4()}.json"
        self._write_json(path, value)
        return path

    def _plan(self, work_item_id: str) -> dict[str, object]:
        profile = json.loads(self.profile.read_text(encoding="utf-8"))
        context = json.loads(
            (
                self.profile.parent
                / profile["project_context"]["artifact_ref"]
            ).read_text(encoding="utf-8")
        )
        facts = next(item for item in context["sections"] if item["kind"] == "facts")
        return {
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
            "plan_version": "1.0",
            "request_id": str(uuid.uuid4()),
            "work_item_id": work_item_id,
            "run_mode": "fixture",
            "objective": "Produce one bounded synthetic result package for human review.",
            "project_context": profile["project_context"],
            "diagnosis": {
                "problem": "The fixture project needs one traceable route output.",
                "controllable_bottleneck": "The decision lacks a revision-bound result package.",
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
                "rationale": "This route is the smallest complete fixture for the decision.",
                "alternatives": [
                    {
                        "route_id": "market-positioning",
                        "reason_not_selected": "Positioning would exceed the bounded fixture question.",
                    }
                ],
                "step_activation": [],
            },
            "operating_contract": operating_contract(
                profile, "2026-09-01T12:00:00Z"
            ),
            "requested_at": "2026-09-01T12:00:00Z",
        }

    def _prepare(self, work_item_id: str, *, legacy: bool = False) -> dict[str, object]:
        prepared = prepare_run(
            self.workspace,
            self.profile,
            self._temporary_json(self._plan(work_item_id)),
        )
        if legacy:
            # Re-create only this test's synthetic initial run in the historical
            # format. Reissue real writer receipts and every affected hash; do
            # not edit receipted bytes or bypass the runtime reader's checks.
            replacements: dict[str, str] = {}
            writes = []
            for item in prepared["writer_receipt"]["writes"]:
                content = (self.state / item["path"]).read_text(encoding="utf-8")
                if item["path"].startswith("records/coordinator-plans/"):
                    plan = json.loads(content)
                    del plan["route_decision"]["step_activation"]
                    content = json.dumps(plan, sort_keys=True, indent=2) + "\n"
                for old_hash, new_hash in replacements.items():
                    content = content.replace(old_hash, new_hash)
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                replacements[item["sha256"]] = digest
                writes.append({
                    "path": item["path"], "mode": "create", "content": content,
                    "content_sha256": digest, "expected_sha256": None,
                })
            shutil.rmtree(self.state)
            receipt = prepared["writer_receipt"]
            prepared["writer_receipt"] = apply_request(
                self.workspace, self.profile, self._temporary_json({
                    "writer_request_version": "1.0",
                    "project_id": receipt["project_id"],
                    "project_profile_revision": receipt["project_profile_revision"],
                    "run_id": receipt["run_id"],
                    "requested_by": receipt["requested_by"],
                    "idempotency_key": str(uuid.uuid4()), "writes": writes,
                }),
            )
            prepared["manifest"]["artifact_sha256"] = replacements[
                prepared["manifest"]["artifact_sha256"]
            ]
        return prepared

    def _manifest(self, pointer: dict[str, str]) -> dict[str, object]:
        return json.loads(
            (self.state / pointer["artifact_ref"]).read_text(encoding="utf-8")
        )

    def _submission(
        self,
        manifest: dict[str, object],
        step_id: str,
        *,
        contract_id: str,
        artifact_ref: str | None,
        content: str = "synthetic result\n",
        status: str = "completed",
        review_candidate: dict[str, object] | None = None,
    ) -> dict[str, object]:
        task_entry = next(
            item for item in manifest["tasks"] if item["step_id"] == step_id
        )
        task = json.loads(
            (self.state / task_entry["task_ref"]).read_text(encoding="utf-8")
        )
        if status == "completed":
            assert artifact_ref is not None
            evidence_refs = [dict(pointer) for pointer in task["input_artifacts"][:1]]
            content = professional_deliverable_content(
                self.workspace,
                self.profile,
                task_entry,
                task,
                contract_id,
                evidence_refs=evidence_refs,
                fixture_note=content,
                created_at="2026-09-01T12:05:00Z",
            )
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            outputs = [
                {
                    "artifact_ref": artifact_ref,
                    "artifact_revision": "r1",
                    "artifact_sha256": digest,
                    "contract_id": contract_id,
                }
            ]
            artifacts = [
                {
                    "artifact_ref": artifact_ref,
                    "artifact_revision": "r1",
                    "artifact_sha256": digest,
                    "media_type": "application/json",
                    "content": content,
                }
            ]
            checks = [
                {
                    "name": "fixture-contract",
                    "result": "pass",
                    "evidence_ref": evidence_refs[0],
                }
            ]
            output_revision = "r1"
            error = None
        else:
            outputs = []
            artifacts = []
            checks = [
                {"name": "fixture-contract", "result": "fail", "evidence_ref": None}
            ]
            output_revision = None
            error = {
                "class": "contract",
                "message": "Synthetic worker failed its contract check.",
                "retryable": False,
            }
        result = {
            "project_id": manifest["project_id"],
            "project_profile_revision": manifest["project_profile_revision"],
            "result_version": "2.0",
            "run_id": manifest["run_id"],
            "task_id": task_entry["task_id"],
            "task_sha256": task_entry["task_sha256"],
            "step_id": step_id,
            "role_id": task["assigned_role"],
            "status": status,
            "output_artifacts": outputs,
            "evidence_artifacts": evidence_refs if status == "completed" else [],
            "checks": checks,
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
                "output_revision": output_revision,
            },
            "error": error,
            "created_at": "2026-09-01T12:05:00Z",
        }
        submission: dict[str, object] = {
            "submission_version": "1.0",
            "project_id": manifest["project_id"],
            "project_profile_revision": manifest["project_profile_revision"],
            "run_id": manifest["run_id"],
            "task_id": task_entry["task_id"],
            "result": result,
            "artifacts": artifacts,
        }
        if review_candidate is not None:
            submission["review_candidate"] = review_candidate
        return submission

    def _accept(self, submission: dict[str, object]) -> dict[str, object]:
        return accept_result(
            self.workspace, self.profile, self._temporary_json(submission)
        )

    def _evolve_registered_role(self, role_id: str) -> None:
        registry_path = self.workspace / "agents/registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        entry = next(
            item for item in registry["roles"] if item["role_id"] == role_id
        )
        role_path = self.workspace / "agents" / entry["spec_ref"]
        role = json.loads(role_path.read_text(encoding="utf-8"))
        role["quality_rules"].append("Synthetic compatible role evolution.")
        self._write_json(role_path, role)
        entry["sha256"] = hashlib.sha256(role_path.read_bytes()).hexdigest()
        self._write_json(registry_path, registry)

        profile = json.loads(self.profile.read_text(encoding="utf-8"))
        profile["agent_registry"]["artifact_sha256"] = hashlib.sha256(
            registry_path.read_bytes()
        ).hexdigest()
        self._write_json(self.profile, profile)

    def test_empty_runtime_is_read_only(self) -> None:
        result = inspect_runtime(self.workspace, self.profile)

        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["runs"], [])
        self.assertEqual(result["status_counts"]["queued"], 0)
        self.assertFalse(self.state.exists())

    def test_persisted_legacy_run_without_step_activation_remains_readable(self) -> None:
        self._awaiting_review(legacy=True)
        plans = list((self.state / "records/coordinator-plans").glob("*.json"))
        self.assertEqual(len(plans), 1)
        plan = json.loads(plans[0].read_text(encoding="utf-8"))
        self.assertNotIn("step_activation", plan["route_decision"])

        result = inspect_runtime(self.workspace, self.profile)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(result["runs"]), 1)
        self.assertEqual(result["runs"][0]["route_id"], "research-evidence")
        self.assertEqual(result["runs"][0]["run_status"], "awaiting_review")
        # Backward compatibility must not exempt historical plans from receipt
        # verification: even a whitespace edit after acceptance is rejected.
        with plans[0].open("a", encoding="utf-8") as stream:
            stream.write("\n")
        with self.assertRaises(RuntimeStatusError):
            inspect_runtime(self.workspace, self.profile)

    def test_completed_task_keeps_its_receipted_role_after_role_evolution(self) -> None:
        prepared = self._prepare("EX-987")
        first = self._manifest(prepared["manifest"])
        self._accept(
            self._submission(
                first,
                "coordinate",
                contract_id="task-set@1",
                artifact_ref="records/decisions/EX-987-coordinate-r1.md",
            )
        )
        self._evolve_registered_role("growth-coordinator")

        result = inspect_runtime(self.workspace, self.profile)

        run = result["runs"][0]
        self.assertEqual(run["task_counts"]["completed"], 1)
        self.assertEqual(
            [(item["step"], item["role"]) for item in run["dispatchable_tasks"]],
            [("research", "evidence-research")],
        )

    def test_queued_task_must_still_match_the_current_registered_role(self) -> None:
        self._prepare("EX-988")
        self._evolve_registered_role("growth-coordinator")

        with self.assertRaisesRegex(
            RuntimeStatusError, "task role snapshot differs from pinned role"
        ):
            inspect_runtime(self.workspace, self.profile)

    def test_prepared_initial_task_is_exactly_dispatchable(self) -> None:
        prepared = self._prepare("EX-980")

        result = inspect_runtime(self.workspace, self.profile)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["status_counts"]["queued"], 1)
        run = result["runs"][0]
        self.assertEqual(run["run_id"], prepared["run_id"])
        self.assertEqual(run["run_status"], "queued")
        self.assertEqual(
            run["dispatchable_tasks"],
            [
                {
                    "step": "coordinate",
                    "role": "growth-coordinator",
                    "task_ref": prepared["initial_tasks"][0]["task_ref"],
                    "task_sha256": prepared["initial_tasks"][0]["task_sha256"],
                }
            ],
        )
        self.assertIsNone(run["review_pointer"])

    def test_advanced_run_reports_only_the_newly_unblocked_task(self) -> None:
        prepared = self._prepare("EX-981")
        first = self._manifest(prepared["manifest"])
        advanced = self._accept(
            self._submission(
                first,
                "coordinate",
                contract_id="task-set@1",
                artifact_ref="records/decisions/EX-981-coordinate-r1.md",
            )
        )

        result = inspect_runtime(self.workspace, self.profile)

        run = result["runs"][0]
        self.assertEqual(run["run_status"], "running")
        self.assertEqual(run["task_counts"]["completed"], 1)
        self.assertEqual(run["task_counts"]["queued"], 1)
        self.assertEqual(
            [(item["step"], item["role"]) for item in run["dispatchable_tasks"]],
            [("research", "evidence-research")],
        )
        self.assertEqual(
            run["manifest"]["artifact_ref"], advanced["manifest"]["artifact_ref"]
        )

    def _awaiting_review(self, *, legacy: bool = False) -> dict[str, object]:
        prepared = self._prepare("EX-982", legacy=legacy)
        first = self._manifest(prepared["manifest"])
        after_coordinate = self._accept(
            self._submission(
                first,
                "coordinate",
                contract_id="task-set@1",
                artifact_ref="records/decisions/EX-982-coordinate-r1.md",
            )
        )
        second = self._manifest(after_coordinate["manifest"])
        producer_content = "# Synthetic evidence packet\n\nBounded finding.\n"
        research_submission = self._submission(
            second,
            "research",
            contract_id="evidence-packet@1",
            artifact_ref="evidence/packets/EX-982-evidence-r1.md",
            content=producer_content,
        )
        producer = research_submission["result"]["output_artifacts"][0]
        after_research = self._accept(research_submission)
        third = self._manifest(after_research["manifest"])
        candidate = {
            "artifact_ref": producer["artifact_ref"],
            "artifact_revision": producer["artifact_revision"],
            "artifact_sha256": producer["artifact_sha256"],
            "artifact_id": "EX-982-evidence",
            "title": "Synthetic evidence packet",
            "type": "evidence-packet",
            "preview": "A bounded synthetic finding prepared for human review.",
            "evidence": [{"label": "Producer output", "ref": producer["artifact_ref"]}],
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
                contract_id="governance-verdict@1",
                artifact_ref="records/governance/EX-982-verdict-r1.md",
                review_candidate=candidate,
            )
        )
        return final

    def test_awaiting_review_reports_verified_review_pointer(self) -> None:
        final = self._awaiting_review()

        result = inspect_runtime(self.workspace, self.profile)

        run = result["runs"][0]
        self.assertEqual(run["run_status"], "awaiting_review")
        self.assertEqual(result["status_counts"]["awaiting_review"], 1)
        self.assertEqual(run["dispatchable_tasks"], [])
        self.assertEqual(run["review_pointer"], final["review_item"])

    def test_blocked_result_is_reported_without_dispatch(self) -> None:
        prepared = self._prepare("EX-983")
        manifest = self._manifest(prepared["manifest"])
        self._accept(
            self._submission(
                manifest,
                "coordinate",
                contract_id="task-set@1",
                artifact_ref=None,
                status="failed",
            )
        )

        result = inspect_runtime(self.workspace, self.profile)

        run = result["runs"][0]
        self.assertEqual(run["run_status"], "failed")
        self.assertTrue(run["blocked_reason"])
        self.assertEqual(run["dispatchable_tasks"], [])

    def test_tampered_task_and_symlink_fail_closed(self) -> None:
        prepared = self._prepare("EX-984")
        task_ref = prepared["initial_tasks"][0]["task_ref"]
        task_path = self.state / task_ref
        original = task_path.read_bytes()
        task_path.write_bytes(original + b"\n")
        with self.assertRaisesRegex(RuntimeStatusError, "task bytes"):
            inspect_runtime(self.workspace, self.profile)

        task_path.write_bytes(original)
        external = Path(self.temporary.name) / "external-task.json"
        external.write_bytes(original)
        task_path.unlink()
        os.symlink(external, task_path)
        with self.assertRaisesRegex(RuntimeStatusError, "regular file safely|symlink"):
            inspect_runtime(self.workspace, self.profile)

    def test_unreceipted_manifest_revision_fails_closed(self) -> None:
        prepared = self._prepare("EX-985")
        first_path = self.state / prepared["manifest"]["artifact_ref"]
        first_content = first_path.read_bytes()
        second = json.loads(first_content)
        second["manifest_revision"] = 2
        second["previous_manifest"] = {
            "artifact_ref": prepared["manifest"]["artifact_ref"],
            "artifact_revision": "r0001",
            "artifact_sha256": hashlib.sha256(first_content).hexdigest(),
        }
        second_path = first_path.parent / "manifest-r0002.json"
        self._write_json(second_path, second, sort_keys=True)

        with self.assertRaisesRegex(RuntimeStatusError, "no completed Root Writer receipt"):
            inspect_runtime(self.workspace, self.profile)

    def test_cross_project_manifest_and_traversal_profile_fail_closed(self) -> None:
        prepared = self._prepare("EX-986")
        manifest_path = self.state / prepared["manifest"]["artifact_ref"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["project_id"] = "other-project"
        self._write_json(manifest_path, manifest, sort_keys=True)
        with self.assertRaisesRegex(
            RuntimeStatusError, "manifest identity mismatch|project_id does not match"
        ):
            inspect_runtime(self.workspace, self.profile)

        profile = json.loads(self.profile.read_text(encoding="utf-8"))
        profile["writer"]["state_root"] = "projects/example/../other/state"
        self._write_json(self.profile, profile)
        with self.assertRaisesRegex(RuntimeStatusError, "unsafe path segment"):
            inspect_runtime(self.workspace, self.profile)

    def test_cli_prints_machine_readable_status_and_rejection(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(
                [
                    "--workspace",
                    str(self.workspace),
                    "--project-config",
                    str(self.profile),
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "empty")

        profile = json.loads(self.profile.read_text(encoding="utf-8"))
        profile["project_id"] = "Wrong Project"
        self._write_json(self.profile, profile)
        error = io.StringIO()
        with redirect_stderr(error):
            code = main(
                [
                    "--workspace",
                    str(self.workspace),
                    "--project-config",
                    str(self.profile),
                ]
            )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(error.getvalue())["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
