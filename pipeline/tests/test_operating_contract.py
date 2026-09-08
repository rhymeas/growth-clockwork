from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from pipeline.operating_contract import (
    OperatingContractError,
    aggregate_usage,
    enforce_resource_limits,
    enforce_run_limits,
    validate_operating_contract,
)


class OperatingContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.profile_root = Path(self.temporary.name) / "alpha"
        schemas = self.profile_root / "schemas"
        schemas.mkdir(parents=True)
        source = (
            Path(__file__).resolve().parents[2]
            / "contracts"
            / "schemas"
            / "growth-operating-contract.schema.json"
        )
        content = source.read_bytes()
        (schemas / source.name).write_bytes(content)
        (self.profile_root / "schema-registry.json").write_text(
            json.dumps(
                {
                    "registry_version": "1.0",
                    "schemas": {
                        "growth-operating-contract@1": {
                            "path": f"schemas/{source.name}",
                            "sha256": hashlib.sha256(content).hexdigest(),
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        self.profile = self.profile_root / "project.json"
        self.profile.write_text(
            json.dumps(
                {
                    "profile_version": "1.0",
                    "profile_revision": "alpha-profile-v1",
                    "project_id": "alpha-project",
                    "schema_registry": "schema-registry.json",
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def contract(self) -> dict[str, object]:
        return {
            "project_id": "alpha-project",
            "project_profile_revision": "alpha-profile-v1",
            "contract_version": "1.0",
            "outcome_owner": {
                "role_id": "growth-coordinator",
                "accountability": "Own the bounded decision and its measurable learning outcome.",
            },
            "growth_stage": "validation",
            "decision_question": "Which single activation bottleneck should the next experiment address?",
            "success_signal": {
                "name": "qualified activation",
                "population": "new eligible users",
                "window": "14 days",
                "decision_rule": "Proceed only when the pre-registered threshold is met.",
                "source_ref": None,
            },
            "guardrails": [
                {
                    "name": "privacy",
                    "failure_condition": "Raw personal data enters a persisted artifact.",
                    "response": "stop",
                }
            ],
            "stop_conditions": ["Evidence remains insufficient after two semantic revisions."],
            "resource_limits": {
                "max_duration_ms": 600000,
                "max_input_tokens": 88000,
                "max_output_tokens": 22000,
                "max_sources": 50,
                "max_variable_external_cost_eur": 0,
                "max_attempts_per_task": 2,
            },
            "authority": {
                "allowed_actions": ["read_project", "write_internal_artifacts"],
                "forbidden_actions": ["publish", "spend", "contact_people"],
                "external_side_effects": False,
                "may_publish": False,
                "may_spend": False,
                "may_contact_people": False,
                "may_use_unofficial_collection": False,
            },
            "data_policy": {
                "allowed_evidence_grades": ["authoritative", "verified", "directional"],
                "raw_personal_data_stored": False,
                "redaction_required": True,
                "retention_days": 30,
                "permitted_destinations": ["evidence", "records", "memory", "outbox"],
            },
            "proof_requirements": [
                "source_hashes",
                "professional_contract_checks",
                "governance_verdict",
                "human_exact_revision",
            ],
            "learning_contract": {
                "learning_record_required": True,
                "causal_claim_requires_experiment": True,
                "destination": "memory/records",
                "next_decision_required": True,
            },
            "created_at": "2026-08-31T12:00:00Z",
        }

    def test_complete_contract_passes(self) -> None:
        validate_operating_contract(
            self.profile,
            self.contract(),
            route_role_ids={"growth-coordinator", "governance-reviewer"},
        )

    def test_outcome_owner_must_be_active(self) -> None:
        with self.assertRaisesRegex(OperatingContractError, "outcome owner"):
            validate_operating_contract(
                self.profile,
                self.contract(),
                route_role_ids={"governance-reviewer"},
            )

    def test_unofficial_sources_need_explicit_exploratory_lane(self) -> None:
        contract = self.contract()
        contract["authority"]["may_use_unofficial_collection"] = True  # type: ignore[index]
        with self.assertRaisesRegex(OperatingContractError, "exploratory"):
            validate_operating_contract(self.profile, contract)

    def test_required_proof_cannot_be_omitted(self) -> None:
        contract = self.contract()
        contract["proof_requirements"].remove("governance_verdict")  # type: ignore[union-attr]
        with self.assertRaisesRegex(OperatingContractError, "mandatory proof"):
            validate_operating_contract(self.profile, contract)

    def test_aggregate_usage_and_hard_limit(self) -> None:
        totals = aggregate_usage(
            [
                {
                    "usage": {
                        "duration_ms": 400000,
                        "input_tokens": 60000,
                        "output_tokens": 10000,
                        "variable_external_cost_eur": 0,
                    }
                },
                {
                    "usage": {
                        "duration_ms": 250000,
                        "input_tokens": 30000,
                        "output_tokens": 5000,
                        "variable_external_cost_eur": 0,
                    }
                },
            ]
        )
        with self.assertRaisesRegex(OperatingContractError, "duration_ms, input_tokens"):
            enforce_resource_limits(self.contract(), totals)

    def test_native_codex_keeps_unknown_tokens_honest_and_cost_known(self) -> None:
        contract = self.contract()
        contract["resource_limits"]["usage_policy"] = "codex_subscription"
        validate_operating_contract(self.profile, contract)
        result = {
            "task_id": "TASK-001", "attempts": 1,
            "provenance": {"provider": "codex"},
            "usage": {"duration_ms": 10, "input_tokens": None,
                      "output_tokens": None, "variable_external_cost_eur": 0},
        }
        totals = enforce_run_limits(contract, [result], source_refs=[])
        self.assertIsNone(totals["input_tokens"])
        self.assertIsNone(totals["output_tokens"])
        self.assertEqual(totals["variable_external_cost_eur"], 0)
        result["usage"]["variable_external_cost_eur"] = None
        with self.assertRaisesRegex(OperatingContractError, "known zero"):
            enforce_run_limits(contract, [result], source_refs=[])
        result["usage"]["variable_external_cost_eur"] = 0
        result["provenance"]["provider"] = "external-provider"
        with self.assertRaisesRegex(OperatingContractError, "native codex"):
            enforce_run_limits(contract, [result], source_refs=[])
        result["provenance"]["provider"] = "codex"
        result["usage"]["duration_ms"] = contract["resource_limits"]["max_duration_ms"] + 1
        with self.assertRaisesRegex(OperatingContractError, "duration_ms"):
            enforce_run_limits(contract, [result], source_refs=[])

    def test_unknown_usage_does_not_become_zero_when_aggregated(self) -> None:
        totals = aggregate_usage([
            {"usage": {"duration_ms": 1, "input_tokens": None,
                       "output_tokens": 10, "variable_external_cost_eur": 0}},
            {"usage": {"duration_ms": 2, "input_tokens": 20,
                       "output_tokens": None, "variable_external_cost_eur": 0}},
        ])
        self.assertIsNone(totals["input_tokens"])
        self.assertIsNone(totals["output_tokens"])
        self.assertEqual(totals["duration_ms"], 3)

    def test_run_limits_require_metered_usage_and_bound_attempts_and_sources(self) -> None:
        result = {
            "task_id": "TASK-001",
            "attempts": 1,
            "usage": {
                "duration_ms": 10,
                "input_tokens": None,
                "output_tokens": 5,
                "variable_external_cost_eur": 0,
            },
        }
        with self.assertRaisesRegex(OperatingContractError, "unmetered"):
            enforce_run_limits(self.contract(), [result], source_refs=[])

        result["usage"]["input_tokens"] = 5
        result["attempts"] = 2
        contract = self.contract()
        contract["resource_limits"]["max_attempts_per_task"] = 1  # type: ignore[index]
        with self.assertRaisesRegex(OperatingContractError, "attempt limit"):
            enforce_run_limits(contract, [result], source_refs=[])

        result["attempts"] = 1
        contract["resource_limits"]["max_sources"] = 1  # type: ignore[index]
        with self.assertRaisesRegex(OperatingContractError, "source limit"):
            enforce_run_limits(contract, [result], source_refs=["a", "b"])


if __name__ == "__main__":
    unittest.main()
