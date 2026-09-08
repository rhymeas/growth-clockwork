from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid

from pipeline.coordinator import CoordinatorError, DISCOVERY_RESTRICTION, prepare_run
from pipeline.tests.operating_fixture import operating_contract
from pipeline.tests.draft_project_fixture import create_draft_project


SOURCE_WORKSPACE = Path(__file__).resolve().parents[2]


class CoordinatorPrepareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name) / "workspace"
        self.workspace.mkdir()
        (self.workspace / "AGENTS.md").write_text("# Test\n", encoding="utf-8")
        shutil.copytree(SOURCE_WORKSPACE / "agents", self.workspace / "agents")
        shutil.copytree(
            SOURCE_WORKSPACE / "projects/example",
            self.workspace / "projects/example",
        )
        self.example_profile = self.workspace / "projects/example/project.json"
        self.draft_profile = self._create_draft_profile()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _plan(
        self,
        profile_path: Path,
        *,
        work_item_id: str,
        route_id: str = "research-evidence",
        request_id: str | None = None,
        run_mode: str = "fixture",
    ) -> dict[str, object]:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        manifest_path = profile_path.parent / profile["project_context"]["artifact_ref"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        evidence = next(
            section for section in manifest["sections"] if section["kind"] == "facts"
        )
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
            "request_id": request_id or str(uuid.uuid4()),
            "work_item_id": work_item_id,
            "run_mode": run_mode,
            "objective": "Answer one bounded synthetic evidence question for review.",
            "project_context": profile["project_context"],
            "diagnosis": {
                "problem": "The synthetic project needs one traceable research answer.",
                "controllable_bottleneck": "The decision currently lacks a bounded evidence packet.",
                "evidence_refs": [
                    {
                        "artifact_ref": evidence["artifact_ref"],
                        "artifact_revision": evidence["artifact_revision"],
                        "artifact_sha256": evidence["artifact_sha256"],
                    }
                ],
                "assumptions": ["All project inputs are synthetic fixtures."],
                "confidence": "medium",
            },
            "route_decision": {
                "selected_route_id": route_id,
                "rationale": "Research is the smallest route that closes the stated evidence gap.",
                "alternatives": [
                    {
                        "route_id": "market-positioning",
                        "reason_not_selected": "Positioning would be premature before evidence synthesis.",
                    }
                ],
                "step_activation": step_activation,
            },
            "operating_contract": operating_contract(
                profile, "2026-09-01T00:30:00Z"
            ),
            "requested_at": "2026-09-01T00:30:00Z",
        }

    def _plan_path(self, value: dict[str, object]) -> Path:
        path = Path(self.temporary.name) / f"plan-{uuid.uuid4()}.json"
        self._write_json(path, value)
        return path

    def _create_draft_profile(self) -> Path:
        draft_root = self.workspace / "projects/draft-example"
        shutil.copytree(self.workspace / "projects/example", draft_root)
        shutil.rmtree(draft_root / "state", ignore_errors=True)

        manifest_path = (
            draft_root / "context/manifests/CTX-EXAMPLE-0001.fixture-r1.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["status"] = "draft"
        self._write_json(manifest_path, manifest)

        profile_path = draft_root / "project.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["project_context"]["artifact_sha256"] = hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest()
        profile["writer"]["state_root"] = "projects/draft-example/state"
        self._write_json(profile_path, profile)
        return profile_path

    def _discovery_plan(self, route_id: str = "content-channel") -> tuple[Path, dict]:
        profile_path = create_draft_project(self.workspace)
        profile_root = profile_path.parent
        plan = self._plan(
            profile_path, work_item_id="RE-900", route_id=route_id,
            run_mode="discovery",
        )
        manifest = json.loads(
            (profile_root / plan["project_context"]["artifact_ref"]).read_text()
        )
        authority = next(item for item in manifest["sections"] if item["kind"] == "authority")
        plan["diagnosis"]["evidence_refs"] = [
            {key: authority[key] for key in ("artifact_ref", "artifact_revision", "artifact_sha256")}
        ]
        plan["diagnosis"]["assumptions"] = ["Audience context is an unvalidated hypothesis."]
        plan["objective"] = "Prepare one useful lesson from primary evidence without product claims."
        return profile_path, plan

    def test_discovery_prepares_content_without_approving_draft_context(self) -> None:
        profile_path, plan = self._discovery_plan()
        plan_path = self._plan_path(plan)
        result = prepare_run(self.workspace, profile_path, plan_path)
        self.assertEqual(result, prepare_run(self.workspace, profile_path, plan_path))
        state = profile_path.parent / "state"
        manifest = json.loads((state / result["manifest"]["artifact_ref"]).read_text())
        context = json.loads((state / manifest["project_context"]["artifact_ref"]).read_text())
        task = json.loads((state / result["initial_tasks"][0]["task_ref"]).read_text())
        self.assertEqual(manifest["run_mode"], "discovery")
        self.assertEqual(context["status"], "draft")
        self.assertIn(DISCOVERY_RESTRICTION, manifest["objective"])
        self.assertIn(DISCOVERY_RESTRICTION, task["objective"])
        self.assertFalse(task["authority"]["may_publish"])

    def test_discovery_cannot_enter_positioning_route(self) -> None:
        profile_path, plan = self._discovery_plan("market-positioning")
        with self.assertRaisesRegex(CoordinatorError, "discovery mode supports only"):
            prepare_run(self.workspace, profile_path, self._plan_path(plan))
        self.assertFalse((profile_path.parent / "state").exists())

    def test_discovery_diagnosis_cannot_cite_blocked_facts(self) -> None:
        profile_path, plan = self._discovery_plan()
        manifest = json.loads(
            (profile_path.parent / plan["project_context"]["artifact_ref"]).read_text()
        )
        facts = next(item for item in manifest["sections"] if item["kind"] == "facts")
        plan["diagnosis"]["evidence_refs"] = [
            {key: facts[key] for key in ("artifact_ref", "artifact_revision", "artifact_sha256")}
        ]
        with self.assertRaisesRegex(CoordinatorError, "evidence is not an exact artifact"):
            prepare_run(self.workspace, profile_path, self._plan_path(plan))
        self.assertFalse((profile_path.parent / "state").exists())

    def test_prepare_materializes_only_initial_wave_and_completed_receipt(self) -> None:
        plan = self._plan(self.example_profile, work_item_id="EX-900")
        result = prepare_run(
            self.workspace, self.example_profile, self._plan_path(plan)
        )

        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["route_id"], "research-evidence")
        self.assertEqual(
            [task["step_id"] for task in result["initial_tasks"]], ["coordinate"]
        )
        state = self.workspace / "projects/example/state"
        manifest_path = state / result["manifest"]["artifact_ref"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "queued")
        self.assertEqual(manifest["manifest_revision"], 1)
        self.assertEqual(len(manifest["planned_steps"]), 3)
        self.assertEqual(len(manifest["tasks"]), 1)
        receipt_paths = {
            item["path"] for item in result["writer_receipt"]["writes"]
        }
        self.assertIn(result["manifest"]["artifact_ref"], receipt_paths)

    def test_identical_prepare_is_idempotent(self) -> None:
        plan = self._plan(self.example_profile, work_item_id="EX-901")
        path = self._plan_path(plan)

        first = prepare_run(self.workspace, self.example_profile, path)
        second = prepare_run(self.workspace, self.example_profile, path)

        self.assertEqual(first, second)

    def test_same_request_id_with_changed_plan_is_rejected(self) -> None:
        request_id = str(uuid.uuid4())
        first = self._plan(
            self.example_profile,
            work_item_id="EX-902",
            request_id=request_id,
        )
        prepare_run(
            self.workspace, self.example_profile, self._plan_path(first)
        )
        changed = dict(first)
        changed["objective"] = "Answer a different synthetic evidence question for review."

        with self.assertRaisesRegex(CoordinatorError, "another request"):
            prepare_run(
                self.workspace, self.example_profile, self._plan_path(changed)
            )

    def test_draft_context_is_rejected_before_state_write(self) -> None:
        plan = self._plan(
            self.draft_profile,
            work_item_id="EX-900",
            run_mode="live",
        )

        with self.assertRaisesRegex(CoordinatorError, "draft, not ready"):
            prepare_run(
                self.workspace, self.draft_profile, self._plan_path(plan)
            )

        self.assertFalse((self.workspace / "projects/draft-example/state").exists())

    def test_plan_cannot_select_unpinned_context(self) -> None:
        plan = self._plan(self.example_profile, work_item_id="EX-903")
        plan["project_context"] = dict(plan["project_context"])
        plan["project_context"]["artifact_sha256"] = "0" * 64

        with self.assertRaisesRegex(CoordinatorError, "profile-pinned context"):
            prepare_run(
                self.workspace, self.example_profile, self._plan_path(plan)
            )

    def test_plan_requires_every_optional_step_activation(self) -> None:
        plan = self._plan(
            self.example_profile,
            work_item_id="EX-904",
            route_id="launch-readiness",
        )
        plan["route_decision"]["step_activation"].pop()

        with self.assertRaisesRegex(
            CoordinatorError, "missing optional step activation"
        ):
            prepare_run(
                self.workspace, self.example_profile, self._plan_path(plan)
            )

    def test_plan_rejects_unknown_step_activation(self) -> None:
        plan = self._plan(
            self.example_profile,
            work_item_id="EX-905",
            route_id="launch-readiness",
        )
        plan["route_decision"]["step_activation"].append(
            {
                "step_id": "unknown-specialist",
                "decision": "skip",
                "reason": "Negative fixture.",
            }
        )

        with self.assertRaisesRegex(
            CoordinatorError, "only an optional route step"
        ):
            prepare_run(
                self.workspace, self.example_profile, self._plan_path(plan)
            )


if __name__ == "__main__":
    unittest.main()
