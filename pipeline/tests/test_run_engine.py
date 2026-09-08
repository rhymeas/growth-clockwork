from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid

from pipeline.coordinator import prepare_run
from pipeline.run_engine import RunEngineError, accept_result
from pipeline.tests.professional_fixture import professional_deliverable_content
from pipeline.tests.operating_fixture import operating_contract


SOURCE_WORKSPACE = Path(__file__).resolve().parents[2]


class RunEngineTests(unittest.TestCase):
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

    def _plan(self, work_item_id: str, route_id: str) -> dict[str, object]:
        profile = json.loads(self.profile.read_text(encoding="utf-8"))
        context_path = self.profile.parent / profile["project_context"]["artifact_ref"]
        context = json.loads(context_path.read_text(encoding="utf-8"))
        facts = next(item for item in context["sections"] if item["kind"] == "facts")
        alternatives = {
            "research-evidence": "market-positioning",
            "product-funnel-experiment": "research-evidence",
            "launch-readiness": "research-evidence",
        }
        route = json.loads(
            (self.workspace / "agents/routes" / f"{route_id}.json").read_text(
                encoding="utf-8"
            )
        )
        step_activation = [
            {
                "step_id": node["node_id"],
                "decision": "skip",
                "reason": "The bounded fixture objective does not require this optional specialist.",
            }
            for node in route["nodes"]
            if node["optional"]
        ]
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
                "selected_route_id": route_id,
                "rationale": "This route is the smallest complete fixture for the decision.",
                "alternatives": [
                    {
                        "route_id": alternatives[route_id],
                        "reason_not_selected": (
                            "The alternative does not match the bounded fixture question."
                        ),
                    }
                ],
                "step_activation": step_activation,
            },
            "operating_contract": operating_contract(
                profile, "2026-09-01T08:00:00Z"
            ),
            "requested_at": "2026-09-01T08:00:00Z",
        }

    def _prepare(self, work_item_id: str, route_id: str) -> dict[str, object]:
        return prepare_run(
            self.workspace,
            self.profile,
            self._temporary_json(self._plan(work_item_id, route_id)),
        )

    def _prepare_plan(self, plan: dict[str, object]) -> dict[str, object]:
        return prepare_run(
            self.workspace,
            self.profile,
            self._temporary_json(plan),
        )

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
        evidence_refs: list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        task_entry = next(
            item for item in manifest["tasks"] if item["step_id"] == step_id
        )
        task = json.loads(
            (self.state / task_entry["task_ref"]).read_text(encoding="utf-8")
        )
        if status == "completed":
            assert artifact_ref is not None
            selected_evidence = (
                [dict(pointer) for pointer in task["input_artifacts"][:1]]
                if evidence_refs is None
                else evidence_refs
            )
            content = professional_deliverable_content(
                self.workspace,
                self.profile,
                task_entry,
                task,
                contract_id,
                evidence_refs=selected_evidence,
                fixture_note=content,
            )
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            output_artifacts = [
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
                    "evidence_ref": selected_evidence[0] if selected_evidence else None,
                }
            ]
            error = None
            output_revision = "r1"
        else:
            output_artifacts = []
            artifacts = []
            checks = [{"name": "fixture-contract", "result": "fail", "evidence_ref": None}]
            error = {
                "class": "contract",
                "message": "Synthetic worker failed its contract check.",
                "retryable": False,
            }
            output_revision = None
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
            "output_artifacts": output_artifacts,
            "evidence_artifacts": selected_evidence if status == "completed" else [],
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
            "created_at": "2026-09-01T08:05:00Z",
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

    def _repin_current_registry(self, registry: dict[str, object]) -> None:
        registry_path = self.workspace / "agents/registry.json"
        self._write_json(registry_path, registry)
        profile = json.loads(self.profile.read_text(encoding="utf-8"))
        profile["agent_registry"]["artifact_sha256"] = hashlib.sha256(
            registry_path.read_bytes()
        ).hexdigest()
        self._write_json(self.profile, profile)

    def _evolve_current_role(self, role_id: str) -> None:
        registry_path = self.workspace / "agents/registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        entry = next(
            item for item in registry["roles"] if item["role_id"] == role_id
        )
        role_path = self.workspace / "agents" / entry["spec_ref"]
        role = json.loads(role_path.read_text(encoding="utf-8"))
        role["quality_rules"].append("Compatible current-registry evolution.")
        self._write_json(role_path, role)
        entry["sha256"] = hashlib.sha256(role_path.read_bytes()).hexdigest()
        self._repin_current_registry(registry)

    def _evolve_current_route(self, route_id: str) -> None:
        registry_path = self.workspace / "agents/registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        entry = next(
            item for item in registry["routes"] if item["route_id"] == route_id
        )
        route_path = self.workspace / "agents" / entry["spec_ref"]
        route = json.loads(route_path.read_text(encoding="utf-8"))
        route["nodes"][1]["objective"] = (
            "CURRENT ROUTE EVOLUTION: use semantics created after this run began."
        )
        self._write_json(route_path, route)
        entry["sha256"] = hashlib.sha256(route_path.read_bytes()).hexdigest()
        self._repin_current_registry(registry)

    def _mutate_frozen_role(
        self,
        prepared: dict[str, object],
        role_id: str,
        mutation,
        *,
        make_receipt_consistent: bool,
    ) -> None:
        run_id = prepared["run_id"]
        role_ref = f"records/run-inputs/{run_id}/agents/roles/{role_id}.json"
        registry_ref = f"records/run-inputs/{run_id}/agents/registry.json"
        role_path = self.state / role_ref
        role = json.loads(role_path.read_text(encoding="utf-8"))
        mutation(role)
        self._write_json(role_path, role)
        if not make_receipt_consistent:
            return

        registry_path = self.state / registry_ref
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        entry = next(
            item for item in registry["roles"] if item["role_id"] == role_id
        )
        entry["sha256"] = hashlib.sha256(role_path.read_bytes()).hexdigest()
        self._write_json(registry_path, registry)

        replacements = {
            role_ref: role_path.read_bytes(),
            registry_ref: registry_path.read_bytes(),
        }
        receipt_dir = self.state / "clockwork/run-receipts" / run_id
        for receipt_path in receipt_dir.glob("writer-*.json"):
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            changed = False
            for write in receipt["writes"]:
                content = replacements.get(write["path"])
                if content is None:
                    continue
                write["sha256"] = hashlib.sha256(content).hexdigest()
                write["bytes"] = len(content)
                changed = True
            if changed:
                self._write_json(receipt_path, receipt)

    def test_sequential_research_route_reaches_review_with_exact_lineage(self) -> None:
        prepared = self._prepare("EX-950", "research-evidence")
        first = self._manifest(prepared["manifest"])
        coordinate = self._submission(
            first,
            "coordinate",
            contract_id="task-set@1",
            artifact_ref="records/decisions/EX-950-coordinate-r1.md",
        )
        after_coordinate = self._accept(coordinate)
        self.assertEqual(after_coordinate["run_status"], "running")
        self.assertEqual(
            [item["step_id"] for item in after_coordinate["new_tasks"]],
            ["research"],
        )

        second = self._manifest(after_coordinate["manifest"])
        research = self._submission(
            second,
            "research",
            contract_id="evidence-packet@1",
            artifact_ref="evidence/packets/EX-950-evidence-r1.md",
            content="Bounded synthetic finding.",
        )
        producer_content = research["artifacts"][0]["content"]
        producer_pointer = research["result"]["output_artifacts"][0]
        after_research = self._accept(research)
        self.assertEqual(
            [item["step_id"] for item in after_research["new_tasks"]],
            ["governance-final"],
        )

        third = self._manifest(after_research["manifest"])
        candidate = {
            "artifact_ref": producer_pointer["artifact_ref"],
            "artifact_revision": producer_pointer["artifact_revision"],
            "artifact_sha256": producer_pointer["artifact_sha256"],
            "artifact_id": "EX-950-evidence",
            "title": "Synthetic evidence packet",
            "type": "evidence-packet",
            "preview": "A bounded synthetic finding prepared for human review.",
            "evidence": [
                {"label": "Producer output", "ref": producer_pointer["artifact_ref"]}
            ],
            "quality_checks": [
                {
                    "name": "Governance fixture",
                    "status": "pass",
                    "detail": "The synthetic governance result passed.",
                }
            ],
        }
        governance = self._submission(
            third,
            "governance-final",
            contract_id="governance-verdict@1",
            artifact_ref="records/governance/EX-950-verdict-r1.md",
            review_candidate=candidate,
        )
        for change, expected_error in [
            ({"verdict": "block"}, "pass verdict"),
            ({"reviewed_artifact": {"artifact_ref": "unrelated.json"}}, "exact task input"),
        ]:
            with self.subTest(change=change):
                invalid = json.loads(json.dumps(governance))
                output = json.loads(invalid["artifacts"][0]["content"])
                output["payload"].update(change)
                content = json.dumps(output) + "\n"
                digest = hashlib.sha256(content.encode()).hexdigest()
                invalid["artifacts"][0].update(content=content, artifact_sha256=digest)
                invalid["result"]["output_artifacts"][0]["artifact_sha256"] = digest
                with self.assertRaisesRegex(RunEngineError, expected_error):
                    self._accept(invalid)
        final = self._accept(governance)

        self.assertEqual(final["run_status"], "awaiting_review")
        final_manifest = self._manifest(final["manifest"])
        self.assertEqual(final_manifest["status"], "awaiting_review")
        self.assertTrue(all(item["status"] == "completed" for item in final_manifest["tasks"]))
        review = json.loads(
            (self.state / final["review_item"]["artifact_ref"]).read_text(
                encoding="utf-8"
            )
        )
        copied = (self.state / review["artifact_path"]).read_text(encoding="utf-8")
        self.assertEqual(copied, producer_content)
        lineage = json.loads(
            (self.state / final["lineage"]["artifact_ref"]).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(lineage["source_run_id"], prepared["run_id"])
        self.assertEqual(lineage["source_route_id"], "research-evidence")
        self.assertEqual(
            lineage["producer"]["artifact_ref"],
            {
                "artifact_ref": producer_pointer["artifact_ref"],
                "artifact_revision": producer_pointer["artifact_revision"],
                "artifact_sha256": producer_pointer["artifact_sha256"],
            },
        )
        self.assertEqual(lineage["review_manifest_ref"], final["review_item"])

    def test_product_funnel_coordinate_opens_parallel_wave(self) -> None:
        prepared = self._prepare("EX-951", "product-funnel-experiment")
        manifest = self._manifest(prepared["manifest"])
        submission = self._submission(
            manifest,
            "coordinate",
            contract_id="task-set@1",
            artifact_ref="records/decisions/EX-951-coordinate-r1.md",
        )

        accepted = self._accept(submission)

        self.assertEqual(
            [item["step_id"] for item in accepted["new_tasks"]],
            ["friction-evidence", "baseline"],
        )
        advanced = self._manifest(accepted["manifest"])
        by_step = {item["step_id"]: item for item in advanced["tasks"]}
        self.assertEqual(by_step["friction-evidence"]["status"], "queued")
        self.assertEqual(by_step["baseline"]["status"], "queued")
        self.assertNotIn("experiment-owner", by_step)

        friction = self._submission(
            advanced,
            "friction-evidence",
            contract_id="evidence-packet@1",
            artifact_ref="evidence/packets/EX-951-friction-r1.md",
        )
        after_friction = self._accept(friction)
        self.assertEqual(after_friction["new_tasks"], [])
        waiting_for_baseline = self._manifest(after_friction["manifest"])
        baseline = self._submission(
            waiting_for_baseline,
            "baseline",
            contract_id="validated-snapshot@1",
            artifact_ref="records/learning/EX-951-baseline-r1.md",
        )
        after_baseline = self._accept(baseline)
        self.assertEqual(
            [item["step_id"] for item in after_baseline["new_tasks"]],
            ["experiment-owner"],
        )

    def test_future_role_evolution_uses_the_receipted_frozen_role(self) -> None:
        prepared = self._prepare("EX-961", "research-evidence")
        first = self._manifest(prepared["manifest"])
        frozen_governance = self.state / (
            f"records/run-inputs/{prepared['run_id']}/agents/roles/"
            "governance-reviewer.json"
        )
        frozen_hash = hashlib.sha256(frozen_governance.read_bytes()).hexdigest()
        self._evolve_current_role("governance-reviewer")

        after_coordinate = self._accept(
            self._submission(
                first,
                "coordinate",
                contract_id="task-set@1",
                artifact_ref="records/decisions/EX-961-coordinate-r1.json",
            )
        )
        second = self._manifest(after_coordinate["manifest"])
        after_research = self._accept(
            self._submission(
                second,
                "research",
                contract_id="evidence-packet@1",
                artifact_ref="evidence/packets/EX-961-evidence-r1.json",
            )
        )

        self.assertEqual(
            [item["step_id"] for item in after_research["new_tasks"]],
            ["governance-final"],
        )
        third = self._manifest(after_research["manifest"])
        governance_entry = next(
            item for item in third["tasks"] if item["step_id"] == "governance-final"
        )
        governance_task = json.loads(
            (self.state / governance_entry["task_ref"]).read_text(encoding="utf-8")
        )
        self.assertEqual(
            governance_task["role_contract"]["artifact_sha256"], frozen_hash
        )

    def test_route_evolution_advances_with_the_receipted_frozen_dag(self) -> None:
        prepared = self._prepare("EX-962", "research-evidence")
        first = self._manifest(prepared["manifest"])
        frozen_route = json.loads(
            (
                self.state
                / f"records/run-inputs/{prepared['run_id']}/agents/route.json"
            ).read_text(encoding="utf-8")
        )
        frozen_research_objective = next(
            node["objective"]
            for node in frozen_route["nodes"]
            if node["node_id"] == "research"
        )
        self._evolve_current_route("research-evidence")

        accepted = self._accept(
            self._submission(
                first,
                "coordinate",
                contract_id="task-set@1",
                artifact_ref="records/decisions/EX-962-coordinate-r1.json",
            )
        )

        advanced = self._manifest(accepted["manifest"])
        research_entry = next(
            item for item in advanced["tasks"] if item["step_id"] == "research"
        )
        research_task = json.loads(
            (self.state / research_entry["task_ref"]).read_text(encoding="utf-8")
        )
        self.assertIn(frozen_research_objective, research_task["objective"])
        self.assertNotIn("CURRENT ROUTE EVOLUTION", research_task["objective"])

    def test_unreceipted_frozen_role_tamper_fails_closed(self) -> None:
        prepared = self._prepare("EX-963", "research-evidence")
        first = self._manifest(prepared["manifest"])
        self._mutate_frozen_role(
            prepared,
            "governance-reviewer",
            lambda role: role["quality_rules"].append("Unreceipted mutation."),
            make_receipt_consistent=False,
        )

        with self.assertRaisesRegex(
            RunEngineError, "hash differs from the frozen registry"
        ):
            self._accept(
                self._submission(
                    first,
                    "coordinate",
                    contract_id="task-set@1",
                    artifact_ref="records/decisions/EX-963-coordinate-r1.json",
                )
            )

    def test_frozen_role_identity_authority_and_output_ownership_fail_closed(
        self,
    ) -> None:
        cases = (
            (
                "EX-964",
                lambda role: role.__setitem__("role_id", "evidence-research"),
                "role identity/version mismatch",
            ),
            (
                "EX-965",
                lambda role: role["authority"].__setitem__("may_publish", True),
                "role authority is too broad",
            ),
            (
                "EX-966",
                lambda role: role.__setitem__(
                    "outputs",
                    [
                        {
                            "output_id": "wrong-output",
                            "contract": "evidence-packet@1",
                            "purpose": "A validly shaped but unauthorized output contract.",
                        }
                    ],
                ),
                "unowned deliverable",
            ),
        )
        for work_item_id, mutation, error in cases:
            with self.subTest(work_item_id=work_item_id):
                prepared = self._prepare(work_item_id, "research-evidence")
                first = self._manifest(prepared["manifest"])
                self._mutate_frozen_role(
                    prepared,
                    "governance-reviewer",
                    mutation,
                    make_receipt_consistent=True,
                )
                with self.assertRaisesRegex(RunEngineError, error):
                    self._accept(
                        self._submission(
                            first,
                            "coordinate",
                            contract_id="task-set@1",
                            artifact_ref=(
                                f"records/decisions/{work_item_id}-coordinate-r1.json"
                            ),
                        )
                    )

    def test_cross_project_and_profile_submissions_still_fail_closed(self) -> None:
        prepared = self._prepare("EX-967", "research-evidence")
        first = self._manifest(prepared["manifest"])
        submission = self._submission(
            first,
            "coordinate",
            contract_id="task-set@1",
            artifact_ref="records/decisions/EX-967-coordinate-r1.json",
        )
        wrong_project = json.loads(json.dumps(submission))
        wrong_project["project_id"] = "other-project"
        with self.assertRaisesRegex(RunEngineError, "another project profile"):
            self._accept(wrong_project)

        wrong_profile = json.loads(json.dumps(submission))
        wrong_profile["project_profile_revision"] = "other-profile-r1"
        with self.assertRaisesRegex(RunEngineError, "another project profile"):
            self._accept(wrong_profile)

    def test_launch_route_skips_optional_steps_and_enforces_parallel_cap(self) -> None:
        prepared = self._prepare("EX-958", "launch-readiness")
        manifest = self._manifest(prepared["manifest"])
        planned = {item["step_id"]: item for item in manifest["planned_steps"]}
        self.assertEqual(
            list(planned),
            [
                "coordinate",
                "launch-evidence",
                "product-readiness",
                "measurement-readiness",
                "launch-package",
                "governance-final",
            ],
        )
        self.assertEqual(
            planned["launch-package"]["depends_on"],
            ["launch-evidence", "product-readiness", "measurement-readiness"],
        )

        coordinate = self._submission(
            manifest,
            "coordinate",
            contract_id="task-set@1",
            artifact_ref="records/decisions/EX-958-coordinate-r1.md",
        )
        after_coordinate = self._accept(coordinate)
        self.assertEqual(
            [item["step_id"] for item in after_coordinate["new_tasks"]],
            ["launch-evidence", "product-readiness"],
        )
        first_wave = self._manifest(after_coordinate["manifest"])
        self.assertLessEqual(
            sum(
                item["status"] in {"queued", "running"}
                for item in first_wave["tasks"]
            ),
            2,
        )
        self.assertNotIn(
            "measurement-readiness",
            {item["step_id"] for item in first_wave["tasks"]},
        )

        evidence = self._submission(
            first_wave,
            "launch-evidence",
            contract_id="evidence-packet@1",
            artifact_ref="evidence/packets/EX-958-launch-r1.md",
        )
        after_evidence = self._accept(evidence)
        self.assertEqual(
            [item["step_id"] for item in after_evidence["new_tasks"]],
            ["measurement-readiness"],
        )
        second_wave = self._manifest(after_evidence["manifest"])
        self.assertLessEqual(
            sum(
                item["status"] in {"queued", "running"}
                for item in second_wave["tasks"]
            ),
            2,
        )

    def test_invalid_artifact_hash_and_write_root_are_rejected_before_advance(self) -> None:
        prepared = self._prepare("EX-952", "research-evidence")
        manifest = self._manifest(prepared["manifest"])
        bad_hash = self._submission(
            manifest,
            "coordinate",
            contract_id="task-set@1",
            artifact_ref="records/decisions/EX-952-coordinate-r1.md",
        )
        bad_hash["artifacts"][0]["content"] = "changed bytes\n"
        with self.assertRaisesRegex(RunEngineError, "hash mismatch"):
            self._accept(bad_hash)

        outside = self._submission(
            manifest,
            "coordinate",
            contract_id="task-set@1",
            artifact_ref="content/EX-952-coordinate-r1.md",
        )
        with self.assertRaisesRegex(RunEngineError, "outside task write roots"):
            self._accept(outside)
        manifest_dir = self.state / "records/runs" / prepared["run_id"]
        self.assertEqual(
            sorted(path.name for path in manifest_dir.iterdir()),
            ["manifest-r0001.json"],
        )

    def test_operating_contract_resource_limit_blocks_result_before_write(self) -> None:
        plan = self._plan("EX-959", "research-evidence")
        plan["operating_contract"]["resource_limits"]["max_duration_ms"] = 24
        prepared = self._prepare_plan(plan)
        manifest = self._manifest(prepared["manifest"])
        submission = self._submission(
            manifest,
            "coordinate",
            contract_id="task-set@1",
            artifact_ref="records/decisions/EX-959-coordinate-r1.md",
        )

        with self.assertRaisesRegex(RunEngineError, "duration_ms"):
            self._accept(submission)

        self.assertFalse(
            (self.state / "records/decisions/EX-959-coordinate-r1.md").exists()
        )

    def test_existing_receipted_task_input_is_valid_evidence_without_resubmission(self) -> None:
        prepared = self._prepare("EX-955", "research-evidence")
        manifest = self._manifest(prepared["manifest"])
        task_entry = manifest["tasks"][0]
        task = json.loads(
            (self.state / task_entry["task_ref"]).read_text(encoding="utf-8")
        )
        existing_evidence = task["input_artifacts"][0]
        submission = self._submission(
            manifest,
            "coordinate",
            contract_id="task-set@1",
            artifact_ref="records/decisions/EX-955-coordinate-r1.md",
            evidence_refs=[existing_evidence],
        )

        accepted = self._accept(submission)

        self.assertEqual(accepted["run_status"], "running")
        self.assertEqual(len(submission["artifacts"]), 1)
        self.assertEqual(
            submission["artifacts"][0]["artifact_ref"],
            submission["result"]["output_artifacts"][0]["artifact_ref"],
        )
        self.assertEqual(submission["result"]["evidence_artifacts"], [existing_evidence])

    def test_existing_evidence_with_wrong_hash_is_rejected(self) -> None:
        prepared = self._prepare("EX-956", "research-evidence")
        manifest = self._manifest(prepared["manifest"])
        task_entry = manifest["tasks"][0]
        task = json.loads(
            (self.state / task_entry["task_ref"]).read_text(encoding="utf-8")
        )
        bad_evidence = dict(task["input_artifacts"][0])
        bad_evidence["artifact_sha256"] = "0" * 64
        submission = self._submission(
            manifest,
            "coordinate",
            contract_id="task-set@1",
            artifact_ref="records/decisions/EX-956-coordinate-r1.md",
            evidence_refs=[bad_evidence],
        )

        with self.assertRaisesRegex(RunEngineError, "evidence hash mismatch"):
            self._accept(submission)

        self.assertFalse(
            (self.state / "records/decisions/EX-956-coordinate-r1.md").exists()
        )

    def test_hash_correct_but_unreceipted_evidence_is_rejected(self) -> None:
        prepared = self._prepare("EX-957", "research-evidence")
        manifest = self._manifest(prepared["manifest"])
        evidence_ref = "records/manual/EX-957-unreceipted.md"
        evidence_content = b"manually inserted evidence\n"
        evidence_path = self.state / evidence_ref
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_bytes(evidence_content)
        evidence = {
            "artifact_ref": evidence_ref,
            "artifact_revision": "manual-r1",
            "artifact_sha256": hashlib.sha256(evidence_content).hexdigest(),
        }
        submission = self._submission(
            manifest,
            "coordinate",
            contract_id="task-set@1",
            artifact_ref="records/decisions/EX-957-coordinate-r1.md",
            evidence_refs=[evidence],
        )

        with self.assertRaisesRegex(RunEngineError, "no completed Root Writer receipt"):
            self._accept(submission)

        self.assertFalse(
            (self.state / "records/decisions/EX-957-coordinate-r1.md").exists()
        )

    def test_same_result_replay_is_idempotent_and_changed_terminal_is_rejected(self) -> None:
        prepared = self._prepare("EX-953", "research-evidence")
        manifest = self._manifest(prepared["manifest"])
        submission = self._submission(
            manifest,
            "coordinate",
            contract_id="task-set@1",
            artifact_ref="records/decisions/EX-953-coordinate-r1.md",
        )
        first = self._accept(submission)
        replay = self._accept(submission)
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(first["manifest"], replay["manifest"])

        changed = json.loads(json.dumps(submission))
        changed_value = json.loads(changed["artifacts"][0]["content"])
        changed_value["payload"]["fixture_note"] = "Different immutable result."
        changed_content = (
            json.dumps(
                changed_value, ensure_ascii=False, indent=2, sort_keys=True
            )
            + "\n"
        )
        changed_digest = hashlib.sha256(changed_content.encode("utf-8")).hexdigest()
        changed["result"]["output_artifacts"][0]["artifact_sha256"] = changed_digest
        changed["artifacts"][0]["artifact_sha256"] = changed_digest
        changed["artifacts"][0]["content"] = changed_content
        with self.assertRaisesRegex(RunEngineError, "different terminal result"):
            self._accept(changed)

    def test_legacy_shaped_new_completed_artifact_is_rejected(self) -> None:
        prepared = self._prepare("EX-959", "research-evidence")
        manifest = self._manifest(prepared["manifest"])
        submission = self._submission(
            manifest,
            "coordinate",
            contract_id="task-set@1",
            artifact_ref="records/decisions/EX-959-coordinate-r1.md",
        )
        legacy_content = "legacy markdown without a professional contract\n"
        legacy_hash = hashlib.sha256(legacy_content.encode("utf-8")).hexdigest()
        submission["result"]["output_artifacts"][0]["artifact_sha256"] = legacy_hash
        submission["artifacts"][0]["artifact_sha256"] = legacy_hash
        submission["artifacts"][0]["content"] = legacy_content

        with self.assertRaisesRegex(
            RunEngineError, "professional deliverable rejected"
        ):
            self._accept(submission)

        self.assertFalse(
            (self.state / "records/decisions/EX-959-coordinate-r1.md").exists()
        )

    def test_completed_governance_cannot_bypass_a_required_professional_check(
        self,
    ) -> None:
        prepared = self._prepare("EX-960", "research-evidence")
        first = self._manifest(prepared["manifest"])
        after_coordinate = self._accept(
            self._submission(
                first,
                "coordinate",
                contract_id="task-set@1",
                artifact_ref="records/decisions/EX-960-coordinate-r1.json",
            )
        )
        second = self._manifest(after_coordinate["manifest"])
        research = self._submission(
            second,
            "research",
            contract_id="evidence-packet@1",
            artifact_ref="evidence/packets/EX-960-evidence-r1.json",
        )
        producer = research["result"]["output_artifacts"][0]
        after_research = self._accept(research)
        third = self._manifest(after_research["manifest"])
        candidate = {
            "artifact_ref": producer["artifact_ref"],
            "artifact_revision": producer["artifact_revision"],
            "artifact_sha256": producer["artifact_sha256"],
            "artifact_id": "EX-960-evidence",
            "title": "Synthetic evidence packet",
            "type": "evidence-packet",
            "preview": "A bounded synthetic finding.",
            "evidence": [{"label": "Producer", "ref": producer["artifact_ref"]}],
            "quality_checks": [
                {"name": "Governance", "status": "pass", "detail": "Passed."}
            ],
        }
        governance = self._submission(
            third,
            "governance-final",
            contract_id="governance-verdict@1",
            artifact_ref="records/governance/EX-960-verdict-r1.json",
            review_candidate=candidate,
        )
        deliverable = json.loads(governance["artifacts"][0]["content"])
        deliverable["quality_checks"] = [
            check
            for check in deliverable["quality_checks"]
            if check["check_id"] != "exact_revision"
        ]
        content = (
            json.dumps(deliverable, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        )
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        governance["result"]["output_artifacts"][0]["artifact_sha256"] = digest
        governance["artifacts"][0]["artifact_sha256"] = digest
        governance["artifacts"][0]["content"] = content

        with self.assertRaisesRegex(RunEngineError, "missing required quality checks"):
            self._accept(governance)

    def test_failed_result_blocks_route_without_materializing_dependents(self) -> None:
        prepared = self._prepare("EX-954", "research-evidence")
        manifest = self._manifest(prepared["manifest"])
        submission = self._submission(
            manifest,
            "coordinate",
            contract_id="task-set@1",
            artifact_ref=None,
            status="failed",
        )

        accepted = self._accept(submission)

        self.assertEqual(accepted["run_status"], "failed")
        self.assertEqual(accepted["new_tasks"], [])
        advanced = self._manifest(accepted["manifest"])
        self.assertEqual(advanced["status"], "failed")
        self.assertEqual(len(advanced["tasks"]), 1)
        self.assertEqual(advanced["tasks"][0]["status"], "failed")
        self.assertEqual(
            advanced["blocked_reason"], "Synthetic worker failed its contract check."
        )


if __name__ == "__main__":
    unittest.main()
