from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from pipeline.learning_loop import (
    LearningLoopError,
    OUTCOME_OR_METRIC,
    RELEASE_OUTCOME,
    close_learning_loop,
)
from pipeline.project_memory import validate_memory_record
from pipeline.tests.operating_fixture import operating_contract


SOURCE_WORKSPACE = Path(__file__).resolve().parents[2]


class LearningLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name) / "workspace"
        self.workspace.mkdir()
        (self.workspace / "AGENTS.md").write_text("# Test\n", encoding="utf-8")
        self.profile_root = self.workspace / "projects/alpha"
        schemas = self.profile_root / "schemas"
        schemas.mkdir(parents=True)
        registered: dict[str, dict[str, str]] = {}
        for schema_id, filename in (
            ("growth-operating-contract@1", "growth-operating-contract.schema.json"),
            ("project-memory-record@1", "project-memory-record.schema.json"),
            ("release-outcome@1", "release-outcome.schema.json"),
        ):
            source = SOURCE_WORKSPACE / "contracts/schemas" / filename
            content = source.read_bytes()
            (schemas / filename).write_bytes(content)
            registered[schema_id] = {
                "path": f"schemas/{filename}",
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        (self.profile_root / "schema-registry.json").write_text(
            json.dumps({"registry_version": "1.0", "schemas": registered}),
            encoding="utf-8",
        )
        self.profile = self.profile_root / "project.json"
        self.profile_value = {
            "profile_version": "1.0",
            "profile_revision": "alpha-profile-v1",
            "project_id": "alpha-project",
            "root_role_id": "growth-clockwork-root",
            "schema_registry": "schema-registry.json",
            "writer": {
                "state_root": "projects/alpha/state",
                "allowed_roots": [
                    "clockwork/run-receipts",
                    "memory",
                    "records",
                    "metrics",
                ],
                "append_only_roots": [
                    "clockwork/run-receipts",
                    "memory",
                    "records",
                    "metrics",
                ],
                "receipt_root": "clockwork/run-receipts",
                "max_files_per_request": 8,
                "max_total_bytes": 100000,
            },
        }
        self.profile.write_text(json.dumps(self.profile_value), encoding="utf-8")
        self.state = self.profile_root / "state"
        self.state.mkdir()
        contract = operating_contract(
            self.profile_value,
            "2026-08-31T12:00:00Z",
        )
        self.contract_pointer = self._persist_json(
            "records/coordinator-plans/RUN-source-001.json",
            {
                "project_id": "alpha-project",
                "project_profile_revision": "alpha-profile-v1",
                "operating_contract": contract,
            },
            "contract-r1",
        )
        self.release_outcome_pointer = self._release_outcome()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _content(value: object) -> bytes:
        return (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")

    def _persist_bytes(
        self, artifact_ref: str, content: bytes, artifact_revision: str
    ) -> dict[str, str]:
        path = self.state / artifact_ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {
            "artifact_ref": artifact_ref,
            "artifact_revision": artifact_revision,
            "artifact_sha256": hashlib.sha256(content).hexdigest(),
        }

    def _persist_json(
        self, artifact_ref: str, value: object, artifact_revision: str
    ) -> dict[str, str]:
        return self._persist_bytes(
            artifact_ref, self._content(value), artifact_revision
        )

    def _release_outcome(self, *, project_id: str = "alpha-project") -> dict[str, str]:
        package = self._persist_json(
            "records/release-packages/fixture/release-r1.json",
            {"fixture": "release package"},
            "release-r1",
        )
        adapter = self._persist_json(
            "records/adapter-receipts/adapter-r1.json",
            {"fixture": "adapter receipt"},
            "adapter-r1",
        )
        proof = self._persist_json(
            "records/live-proofs/proof-r1.json",
            {"fixture": "non-live proof"},
            "proof-r1",
        )
        outcome_id = "88888888-8888-4888-8888-888888888888"
        value = {
            "outcome_record_version": "1.0",
            "outcome_id": outcome_id,
            "project_id": project_id,
            "project_profile_revision": "alpha-profile-v1",
            "release_package": package,
            "adapter_receipt": adapter,
            "live_proof": proof,
            "outcome_type": "fixture-observation",
            "statement": (
                "Exact synthetic bytes were copied locally; no live result was measured."
            ),
            "metrics": [],
            "causal_claim": False,
            "external_side_effects": False,
            "recorded_at": "2026-08-31T12:05:00Z",
        }
        return self._persist_json(
            "records/release-outcomes/outcome-r1.json", value, outcome_id
        )

    def _metric_outcome(self) -> dict[str, str]:
        source = self._persist_json(
            "metrics/sources/qualified-activation-r1.json",
            {"metric": "qualified_activation", "value": 3},
            "metric-source-r1",
        )
        return self._persist_json(
            "metrics/outcomes/qualified-activation-r1.json",
            {
                "outcome_artifact_version": "1.0",
                "project_id": "alpha-project",
                "project_profile_revision": "alpha-profile-v1",
                "artifact_type": "metric",
                "proof_layer": "measured_non_live",
                "statement": (
                    "Three synthetic test activations were measured in the bounded fixture."
                ),
                "metrics": [
                    {
                        "metric_id": "qualified_activation",
                        "value": 3,
                        "unit": "count",
                        "population": "synthetic test cases",
                        "window": "one fixture run",
                        "source_ref": source,
                    }
                ],
                "causal_claim": False,
                "recorded_at": "2026-08-31T12:05:00Z",
            },
            "metric-outcome-r1",
        )

    def _evaluation(self) -> dict[str, object]:
        return {
            "evaluation_version": "1.0",
            "memory_id": "MEM-fixture-outcome",
            "memory_revision": "r1",
            "source_run_id": "RUN-source-001",
            "source_task_id": "EX-1000-T010",
            "learning_statement": (
                "The fixture proves an exact local copy only; no live outcome was measured."
            ),
            "claim_mode": "observation",
            "evidence_grade": "verified",
            "decision_eligible": False,
            "causal_claim": False,
            "experiment_evidence": None,
            "confidence": "medium",
            "tags": ["fixture", "outcome"],
            "supersedes": [],
            "unknowns": ["Live behavior remains unmeasured."],
            "assumptions": ["The fixture is intentionally non-live."],
            "evaluation_basis": [
                "The exact release outcome records no metrics or external side effects."
            ],
            "fabricated_values": False,
            "outcome_disposition": "insufficient_evidence",
            "expires_at": None,
            "evaluated_at": "2026-08-31T12:10:00Z",
            "next_decision": {
                "proposal_id": "NEXT-measure-live-outcome",
                "proposal_revision": "r1",
                "decision_question": (
                    "Which bounded non-public measurement can establish a real outcome?"
                ),
                "rationale": (
                    "The pinned fixture outcome contains no live metric observation."
                ),
                "recommended_action": (
                    "Prepare one separately authorized measurement plan without publishing."
                ),
                "success_signal": {
                    "name": "verified outcome observation",
                    "population": "eligible bounded test cases",
                    "window": "one authorized measurement window",
                    "decision_rule": (
                        "Continue only when the exact metric source is pinned and verified."
                    ),
                },
                "stop_condition": (
                    "Stop if measurement requires publication or fabricated values."
                ),
                "owner_role_id": "growth-coordinator",
            },
        }

    def _close(
        self,
        *,
        outcome: dict[str, str] | None = None,
        outcome_kind: str = RELEASE_OUTCOME,
        evaluation: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return close_learning_loop(
            self.workspace,
            self.profile,
            outcome=outcome or self.release_outcome_pointer,
            outcome_kind=outcome_kind,
            operating_contract=self.contract_pointer,
            evaluation=evaluation or self._evaluation(),
        )

    def test_exact_outcome_closes_memory_and_next_decision_in_one_transaction(
        self,
    ) -> None:
        result = self._close()

        memory_path = self.state / result["memory_record"]["artifact_ref"]
        decision_path = self.state / result["next_decision"]["artifact_ref"]
        memory = json.loads(memory_path.read_text(encoding="utf-8"))
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        validate_memory_record(self.profile, memory, verify_evidence=True)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["writer_receipt"]["writes"]), 2)
        self.assertEqual(memory["causal_claim"], False)
        self.assertEqual(memory["decision_eligible"], False)
        self.assertEqual(decision["learning_record"], result["memory_record"])
        self.assertEqual(decision["proof_layer"], "synthetic_fixture")
        self.assertEqual(decision["source_live"], False)
        self.assertEqual(decision["may_execute"], False)
        self.assertEqual(decision["may_publish"], False)
        self.assertEqual(decision["external_side_effects"], False)

        replay = self._close()
        self.assertEqual(replay["writer_receipt"], result["writer_receipt"])

    def test_outcome_hash_mismatch_writes_nothing(self) -> None:
        outcome = dict(self.release_outcome_pointer)
        outcome["artifact_sha256"] = "0" * 64

        with self.assertRaisesRegex(LearningLoopError, "outcome hash mismatch"):
            self._close(outcome=outcome)

        self.assertFalse((self.state / "memory").exists())
        self.assertFalse((self.state / "records/next-decisions").exists())

    def test_causal_claim_without_exact_experiment_evidence_is_rejected(self) -> None:
        evaluation = self._evaluation()
        evaluation["claim_mode"] = "experiment_result"
        evaluation["causal_claim"] = True
        evaluation["decision_eligible"] = True

        with self.assertRaisesRegex(
            LearningLoopError, "causal claims require exact experiment evidence"
        ):
            self._close(
                outcome=self._metric_outcome(),
                outcome_kind=OUTCOME_OR_METRIC,
                evaluation=evaluation,
            )

        self.assertFalse((self.state / "memory").exists())
        self.assertFalse((self.state / "records/next-decisions").exists())

    def test_cross_project_outcome_is_rejected(self) -> None:
        cross_project = self._release_outcome(project_id="beta-project")

        with self.assertRaisesRegex(LearningLoopError, "project"):
            self._close(outcome=cross_project)

        self.assertFalse((self.state / "memory").exists())
        self.assertFalse((self.state / "records/next-decisions").exists())


if __name__ == "__main__":
    unittest.main()
