from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid

from pipeline.coordinator import prepare_run
from pipeline.run_engine import accept_result
from pipeline.tests.professional_fixture import professional_deliverable_content
from pipeline.tests.operating_fixture import operating_contract


SOURCE_WORKSPACE = Path(__file__).resolve().parents[2]


PILOTS = {
    "audience-deep-research": {
        "work_item_id": "EX-973",
        "waves": [
            ["coordinate"],
            ["audience-research"],
            ["audience-synthesis"],
            ["governance-final"],
        ],
        "steps": {
            "coordinate": (
                "task-set@1",
                "records/decisions/EX-973-coordinate-r1.md",
            ),
            "audience-research": (
                "audience-research-package@1",
                "evidence/packets/EX-973-audience-research-r1.md",
            ),
            "audience-synthesis": (
                "audience-change-proposal@1",
                "records/audience/EX-973-audience-r1.md",
            ),
            "governance-final": (
                "governance-verdict@1",
                "records/governance/EX-973-final-r1.md",
            ),
        },
        "producer": "audience-synthesis",
        "final_contract": "audience-change-proposal@1",
        "evidence_step": "audience-research",
        "artifact_id": "EX-973-audience-change",
        "artifact_type": "audience-change-proposal",
    },
    "market-positioning": {
        "work_item_id": "EX-970",
        "waves": [
            ["coordinate"],
            ["market-evidence"],
            ["audience-synthesis"],
            ["positioning"],
            ["governance-final"],
        ],
        "steps": {
            "coordinate": (
                "task-set@1",
                "records/decisions/EX-970-coordinate-r1.md",
            ),
            "market-evidence": (
                "evidence-packet@1",
                "evidence/packets/EX-970-market-evidence-r1.md",
            ),
            "audience-synthesis": (
                "audience-change-proposal@1",
                "records/audience/EX-970-audience-r1.md",
            ),
            "positioning": (
                "positioning-package@1",
                "positioning/packages/EX-970-positioning-r1.md",
            ),
            "governance-final": (
                "governance-verdict@1",
                "records/governance/EX-970-final-r1.md",
            ),
        },
        "producer": "positioning",
        "final_contract": "positioning-package@1",
        "evidence_step": "market-evidence",
        "artifact_id": "EX-970-positioning",
        "artifact_type": "positioning-package",
    },
    "product-funnel-experiment": {
        "work_item_id": "EX-971",
        "waves": [
            ["coordinate"],
            ["friction-evidence", "baseline"],
            ["experiment-owner"],
            ["measurement-design"],
            ["governance-final"],
        ],
        "steps": {
            "coordinate": (
                "task-set@1",
                "records/decisions/EX-971-coordinate-r1.md",
            ),
            "friction-evidence": (
                "evidence-packet@1",
                "evidence/packets/EX-971-friction-r1.md",
            ),
            "baseline": (
                "validated-snapshot@1",
                "records/learning/EX-971-baseline-r1.md",
            ),
            "experiment-owner": (
                "experiment-charter@1",
                "records/experiments/EX-971-charter-r1.md",
            ),
            "measurement-design": (
                "measurement-plan@1",
                "records/learning/EX-971-measurement-r1.md",
            ),
            "governance-final": (
                "governance-verdict@1",
                "records/governance/EX-971-final-r1.md",
            ),
        },
        "producer": "experiment-owner",
        "final_contract": "experiment-charter@1",
        "evidence_step": "friction-evidence",
        "artifact_id": "EX-971-experiment",
        "artifact_type": "experiment-charter",
    },
    "content-channel": {
        "work_item_id": "EX-972",
        "waves": [
            ["coordinate"],
            ["content-evidence"],
            ["audience-refresh"],
            ["positioning-context"],
            ["brief"],
            ["governance-precheck"],
            ["creative"],
            ["editorial"],
            ["governance-final"],
        ],
        "steps": {
            "coordinate": (
                "task-set@1",
                "records/decisions/EX-972-coordinate-r1.md",
            ),
            "content-evidence": (
                "evidence-packet@1",
                "evidence/packets/EX-972-content-evidence-r1.md",
            ),
            "audience-refresh": (
                "audience-change-proposal@1",
                "records/audience/EX-972-audience-r1.md",
            ),
            "positioning-context": (
                "positioning-package@1",
                "positioning/packages/EX-972-context-r1.md",
            ),
            "brief": (
                "lesson-brief@1",
                "content/EX-972-brief-r1.md",
            ),
            "governance-precheck": (
                "governance-verdict@1",
                "records/governance/EX-972-precheck-r1.md",
            ),
            "creative": (
                "creative-package@1",
                "staging/EX-972-creative-r1.md",
            ),
            "editorial": (
                "channel-product@1",
                "content/EX-972-channel-product-r1.md",
            ),
            "governance-final": (
                "governance-verdict@1",
                "records/governance/EX-972-final-r1.md",
            ),
        },
        "producer": "editorial",
        "final_contract": "channel-product@1",
        "evidence_step": "content-evidence",
        "artifact_id": "EX-972-channel-product",
        "artifact_type": "channel-product",
    },
}


class RoutePilotTests(unittest.TestCase):
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

    def _read_state_json(self, artifact_ref: str) -> dict[str, object]:
        return json.loads((self.state / artifact_ref).read_text(encoding="utf-8"))

    def _manifest(self, pointer: dict[str, str]) -> dict[str, object]:
        return self._read_state_json(pointer["artifact_ref"])

    def _plan(self, work_item_id: str, route_id: str) -> dict[str, object]:
        profile = json.loads(self.profile.read_text(encoding="utf-8"))
        route = json.loads(
            (self.workspace / "agents/routes" / f"{route_id}.json").read_text(
                encoding="utf-8"
            )
        )
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
                "controllable_bottleneck": (
                    "The decision lacks a revision-bound result package."
                ),
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
                "rationale": "This route matches the bounded fixture decision.",
                "alternatives": [
                    {
                        "route_id": "research-evidence",
                        "reason_not_selected": (
                            "Research alone does not produce the selected route product."
                        ),
                    }
                ],
                "step_activation": [
                    {
                        "step_id": node["node_id"],
                        "decision": "activate",
                        "reason": "The route pilot exercises every optional specialist.",
                    }
                    for node in route["nodes"]
                    if node["optional"]
                ],
            },
            "operating_contract": operating_contract(
                profile, "2026-09-01T09:00:00Z"
            ),
            "requested_at": "2026-09-01T09:00:00Z",
        }

    def _submission(
        self,
        manifest: dict[str, object],
        step_id: str,
        contract_id: str,
        artifact_ref: str,
        content: str,
        review_candidate: dict[str, object] | None = None,
    ) -> dict[str, object]:
        task_entry = next(
            item for item in manifest["tasks"] if item["step_id"] == step_id
        )
        task = self._read_state_json(task_entry["task_ref"])
        evidence_refs = [dict(pointer) for pointer in task["input_artifacts"][:1]]
        content = professional_deliverable_content(
            self.workspace,
            self.profile,
            task_entry,
            task,
            contract_id,
            evidence_refs=evidence_refs,
            fixture_note=content,
            created_at="2026-09-01T09:05:00Z",
        )
        if contract_id == "governance-verdict@1" and review_candidate is not None:
            value = json.loads(content)
            value["payload"]["reviewed_artifact"] = {
                key: review_candidate[key]
                for key in ("artifact_ref", "artifact_revision", "artifact_sha256")
            }
            content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        output = {
            "artifact_ref": artifact_ref,
            "artifact_revision": "r1",
            "artifact_sha256": digest,
            "contract_id": contract_id,
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
            "status": "completed",
            "output_artifacts": [output],
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
            "created_at": "2026-09-01T09:05:00Z",
        }
        submission = {
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

    def _state_files(self) -> set[str]:
        return {
            path.relative_to(self.state).as_posix()
            for path in self.state.rglob("*")
            if path.is_file()
        }

    def _run_pilot(self, route_id: str) -> dict[str, object]:
        spec = PILOTS[route_id]
        work_item_id = spec["work_item_id"]
        waves = spec["waves"]
        steps = spec["steps"]
        forbidden_before = {
            path
            for path in self._state_files()
            if "publish" in path.lower() or path.startswith("outbox/review-actions/")
        }

        prepared = prepare_run(
            self.workspace,
            self.profile,
            self._temporary_json(self._plan(work_item_id, route_id)),
        )
        manifest_pointer = prepared["manifest"]
        self.assertEqual(
            [item["step_id"] for item in prepared["initial_tasks"]], waves[0]
        )

        output_by_step: dict[str, dict[str, str]] = {}
        content_by_step: dict[str, str] = {}
        observed_waves = [waves[0]]
        final: dict[str, object] | None = None

        for wave_index, wave in enumerate(waves):
            manifest = self._manifest(manifest_pointer)
            queued = [
                item["step_id"]
                for item in manifest["tasks"]
                if item["status"] == "queued"
            ]
            self.assertEqual(queued, wave)

            planned = {item["step_id"]: item for item in manifest["planned_steps"]}
            planned_task_ids = {
                item["step_id"]: f"{work_item_id}-T{index:03d}"
                for index, item in enumerate(manifest["planned_steps"], start=1)
            }
            tasks_by_step = {item["step_id"]: item for item in manifest["tasks"]}
            for step_id in wave:
                task = self._read_state_json(tasks_by_step[step_id]["task_ref"])
                self.assertEqual(
                    task["depends_on"],
                    [planned_task_ids[item] for item in planned[step_id]["depends_on"]],
                )
                self.assertFalse(task["authority"]["external_side_effects"])
                self.assertFalse(task["authority"]["may_publish"])

            for step_index, step_id in enumerate(wave):
                manifest = self._manifest(manifest_pointer)
                contract_id, artifact_ref = steps[step_id]
                content = (
                    f"# Synthetic {route_id} / {step_id}\n\n"
                    f"Exact fixture bytes for {work_item_id}.\n"
                )
                review_candidate = None
                if step_id == "governance-final":
                    producer = output_by_step[spec["producer"]]
                    supporting = output_by_step[spec["evidence_step"]]
                    review_candidate = {
                        "artifact_ref": producer["artifact_ref"],
                        "artifact_revision": producer["artifact_revision"],
                        "artifact_sha256": producer["artifact_sha256"],
                        "artifact_id": spec["artifact_id"],
                        "title": f"Synthetic {route_id} pilot",
                        "type": spec["artifact_type"],
                        "preview": "A bounded synthetic route product for human review.",
                        "evidence": [
                            {
                                "label": "Synthetic route evidence",
                                "ref": supporting["artifact_ref"],
                            }
                        ],
                        "quality_checks": [
                            {
                                "name": "Governance fixture",
                                "status": "pass",
                                "detail": "The exact synthetic revision passed.",
                            }
                        ],
                    }
                submission = self._submission(
                    manifest,
                    step_id,
                    contract_id,
                    artifact_ref,
                    content,
                    review_candidate,
                )
                content_by_step[step_id] = submission["artifacts"][0]["content"]
                output_by_step[step_id] = submission["result"]["output_artifacts"][0]
                accepted = self._accept(submission)
                manifest_pointer = accepted["manifest"]
                final = accepted

                if step_index < len(wave) - 1:
                    self.assertEqual(accepted["new_tasks"], [])
                    partially_complete = self._manifest(manifest_pointer)
                    materialized = {
                        item["step_id"] for item in partially_complete["tasks"]
                    }
                    self.assertTrue(set(waves[wave_index + 1]).isdisjoint(materialized))
                elif wave_index + 1 < len(waves):
                    expected_next = waves[wave_index + 1]
                    self.assertEqual(
                        [item["step_id"] for item in accepted["new_tasks"]],
                        expected_next,
                    )
                    observed_waves.append(expected_next)
                else:
                    self.assertEqual(accepted["new_tasks"], [])

        assert final is not None
        self.assertEqual(observed_waves, waves)
        self.assertEqual(final["run_status"], "awaiting_review")
        final_manifest = self._manifest(final["manifest"])
        self.assertEqual(final_manifest["status"], "awaiting_review")
        self.assertFalse(final_manifest["publish_side_effects"])
        self.assertEqual(len(final_manifest["tasks"]), len(steps))
        self.assertTrue(
            all(item["status"] == "completed" for item in final_manifest["tasks"])
        )

        producer_step = spec["producer"]
        producer_pointer = output_by_step[producer_step]
        self.assertEqual(producer_pointer["contract_id"], spec["final_contract"])
        producer_entry = next(
            item for item in final_manifest["tasks"] if item["step_id"] == producer_step
        )
        producer_result = self._read_state_json(
            producer_entry["result_ref"]["artifact_ref"]
        )
        self.assertEqual(producer_result["output_artifacts"], [producer_pointer])

        review_pointer = final["review_item"]
        review_bytes = (self.state / review_pointer["artifact_ref"]).read_bytes()
        self.assertEqual(hashlib.sha256(review_bytes).hexdigest(), review_pointer["artifact_sha256"])
        review = json.loads(review_bytes)
        copied_bytes = (self.state / review["artifact_path"]).read_bytes()
        expected_bytes = content_by_step[producer_step].encode("utf-8")
        self.assertEqual(copied_bytes, expected_bytes)
        self.assertEqual(hashlib.sha256(copied_bytes).hexdigest(), producer_pointer["artifact_sha256"])
        self.assertEqual(review["artifact_sha256"], producer_pointer["artifact_sha256"])

        lineage_pointer = final["lineage"]
        lineage_bytes = (self.state / lineage_pointer["artifact_ref"]).read_bytes()
        self.assertEqual(hashlib.sha256(lineage_bytes).hexdigest(), lineage_pointer["artifact_sha256"])
        lineage = json.loads(lineage_bytes)
        self.assertEqual(lineage["source_run_id"], prepared["run_id"])
        self.assertEqual(lineage["source_route_id"], route_id)
        self.assertEqual(lineage["producer"]["step_id"], producer_step)
        self.assertEqual(
            lineage["producer"]["artifact_ref"],
            {
                "artifact_ref": producer_pointer["artifact_ref"],
                "artifact_revision": producer_pointer["artifact_revision"],
                "artifact_sha256": producer_pointer["artifact_sha256"],
            },
        )
        governance_entry = next(
            item
            for item in final_manifest["tasks"]
            if item["step_id"] == "governance-final"
        )
        governance_result = self._read_state_json(
            governance_entry["result_ref"]["artifact_ref"]
        )
        self.assertEqual(governance_result["status"], "completed")
        self.assertEqual(
            [item["contract_id"] for item in governance_result["output_artifacts"]],
            ["governance-verdict@1"],
        )
        self.assertTrue(
            all(item["result"] == "pass" for item in governance_result["checks"])
        )
        self.assertEqual(lineage["governance"]["step_id"], "governance-final")
        self.assertEqual(
            lineage["governance"]["result_ref"], governance_entry["result_ref"]
        )
        self.assertEqual(lineage["review_manifest_ref"], review_pointer)

        final_write_paths = {
            item["path"] for item in final["writer_receipt"]["writes"]
        }
        self.assertTrue(
            {
                review["artifact_path"],
                review_pointer["artifact_ref"],
                lineage_pointer["artifact_ref"],
            }.issubset(final_write_paths)
        )
        self.assertFalse(any("publish" in path.lower() for path in final_write_paths))
        forbidden_after = {
            path
            for path in self._state_files()
            if "publish" in path.lower() or path.startswith("outbox/review-actions/")
        }
        self.assertEqual(forbidden_after, forbidden_before)
        return {
            "prepared": prepared,
            "final": final,
            "manifest": final_manifest,
            "waves": observed_waves,
        }

    def test_market_positioning_route_reaches_awaiting_review(self) -> None:
        self._run_pilot("market-positioning")

    def test_audience_deep_research_route_reaches_awaiting_review(self) -> None:
        self._run_pilot("audience-deep-research")

    def test_product_funnel_route_runs_real_parallel_wave(self) -> None:
        result = self._run_pilot("product-funnel-experiment")

        self.assertEqual(
            result["waves"][1], ["friction-evidence", "baseline"]
        )

    def test_content_channel_route_reaches_awaiting_review(self) -> None:
        self._run_pilot("content-channel")


if __name__ == "__main__":
    unittest.main()
