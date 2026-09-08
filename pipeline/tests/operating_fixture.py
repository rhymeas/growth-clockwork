from __future__ import annotations

from typing import Any


def operating_contract(
    profile: dict[str, Any],
    requested_at: str,
    *,
    outcome_owner: str = "growth-coordinator",
    allow_exploratory: bool = False,
) -> dict[str, Any]:
    """Return a complete synthetic operating contract for runtime fixtures."""

    grades = ["authoritative", "verified", "directional"]
    if allow_exploratory:
        grades.append("exploratory")
    return {
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "contract_version": "1.0",
        "outcome_owner": {
            "role_id": outcome_owner,
            "accountability": (
                "Own the bounded synthetic decision and its measurable learning outcome."
            ),
        },
        "growth_stage": "validation",
        "decision_question": (
            "Which bounded evidence-backed action should the synthetic fixture take next?"
        ),
        "success_signal": {
            "name": "decision-ready evidence",
            "population": "the selected synthetic fixture scope",
            "window": "this bounded run",
            "decision_rule": (
                "Succeed only when the exact final artifact passes every required contract."
            ),
            "source_ref": None,
        },
        "guardrails": [
            {
                "name": "truth and privacy",
                "failure_condition": (
                    "A claim lacks evidence or raw personal data enters an artifact."
                ),
                "response": "stop",
            }
        ],
        "stop_conditions": [
            "Stop after two semantic revisions or when required evidence is unavailable."
        ],
        "resource_limits": {
            "max_duration_ms": 3600000,
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
            "may_use_unofficial_collection": allow_exploratory,
        },
        "data_policy": {
            "allowed_evidence_grades": grades,
            "raw_personal_data_stored": False,
            "redaction_required": True,
            "retention_days": 30,
            "permitted_destinations": [
                "evidence",
                "records",
                "memory",
                "staging",
                "outbox",
            ],
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
        "created_at": requested_at,
    }

