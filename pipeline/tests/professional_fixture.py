from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _fixture_value(field_type: str) -> object:
    return {
        "string": "professionally defined synthetic fixture",
        "array": [{"status": "defined"}],
        "object": {"status": "defined"},
        "boolean": False,
        "integer": 1,
        "number": 1.0,
    }[field_type]


def professional_deliverable_content(
    workspace: Path,
    profile_path: Path,
    task_entry: dict[str, Any],
    task: dict[str, Any],
    contract_id: str,
    *,
    evidence_refs: list[dict[str, str]] | None = None,
    artifact_revision: str = "r1",
    fixture_note: str = "Synthetic professional deliverable.",
    created_at: str = "2026-09-01T08:05:00Z",
) -> str:
    """Build an independent complete JSON deliverable for runtime tests."""

    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    requirements_path = (
        workspace
        / profile["professional_contracts"]["requirements"]["artifact_ref"]
    )
    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
    contract = requirements["contracts"][contract_id]
    evidence = (
        [dict(pointer) for pointer in task["input_artifacts"][:1]]
        if evidence_refs is None
        else [dict(pointer) for pointer in evidence_refs]
    )
    payload = {
        field: _fixture_value(field_type)
        for field, field_type in contract["required_payload_fields"].items()
    }
    payload["fixture_note"] = fixture_note
    if contract_id == "governance-verdict@1":
        payload["verdict"] = "pass"
        # Governance fixtures review an actual frozen predecessor, not a token
        # placeholder. Prefer the final producer over its context or task set.
        candidates = []
        state = workspace / profile["writer"]["state_root"]
        for pointer in task["input_artifacts"]:
            try:
                source = json.loads((state / pointer["artifact_ref"]).read_text())
            except (OSError, ValueError):
                continue
            if source.get("contract_id") not in {None, "task-set@1", "governance-verdict@1"}:
                candidates.append(pointer)
        payload["reviewed_artifact"] = dict(candidates[-1] if candidates else task["input_artifacts"][-1])
    value = {
        "deliverable_version": "1.0",
        "project_id": task["project_id"],
        "project_profile_revision": task["project_profile_revision"],
        "run_id": task["run_id"],
        "task_id": task["task_id"],
        "task_sha256": task_entry["task_sha256"],
        "step_id": task["step_id"],
        "role_id": task["assigned_role"],
        "contract_id": contract_id,
        "artifact_revision": artifact_revision,
        "evidence_refs": evidence,
        "unknowns": ["Synthetic fixture does not prove a live outcome."],
        "assumptions": ["All fixture inputs are synthetic."],
        "confidence": "medium",
        "quality_checks": [
            {
                "check_id": check_id,
                "status": "pass",
                "evidence_refs": [dict(pointer) for pointer in evidence],
            }
            for check_id in contract["required_quality_checks"]
        ],
        "payload": payload,
        "created_at": created_at,
    }
    return json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"
