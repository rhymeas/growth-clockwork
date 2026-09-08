#!/usr/bin/env python3
"""Close one outcome into immutable learning and a next-decision proposal.

The module is deliberately a local control-plane boundary. It reads exact
project-state bytes, validates one growth operating contract, accepts only
caller-supplied evaluation fields, and asks the Root Writer to append both
outputs in one request. It does not publish, call a network, or execute the
proposed decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any
import uuid

from pipeline import project_memory, release_core, root_writer
from pipeline.operating_contract import (
    OperatingContractError,
    validate_operating_contract,
)
from pipeline.validate_json_suites import (
    SuiteConfigurationError,
    validate_registered_instance,
)


LOOP_VERSION = "1.0"
RELEASE_OUTCOME = "release-outcome"
OUTCOME_OR_METRIC = "outcome-or-metric"
SOURCE_KINDS = {RELEASE_OUTCOME, OUTCOME_OR_METRIC}
PROOF_LAYERS = {
    "not_measured",
    "synthetic_fixture",
    "measured_non_live",
    "live_outcome",
}
CLAIM_MODES = {"observation", "hypothesis", "experiment_result"}
DISPOSITIONS = {"continue", "revise", "stop", "insufficient_evidence"}
PROPOSAL_ID_RE = re.compile(r"^NEXT-[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
REVISION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ROLE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
RFC3339_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
REQUEST_NAMESPACE = uuid.UUID("65ce42bd-03f9-47bd-b9a2-8bc249dc0e7e")


class LearningLoopError(ValueError):
    """Raised before a learning-loop closure may become canonical state."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json(value: Any) -> bytes:
    try:
        return (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError) as exc:
        raise LearningLoopError("learning-loop values must be UTF-8 JSON") from exc


def _strict_json(content: bytes, label: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-JSON numeric constant {value}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise LearningLoopError(f"cannot parse {label} as strict UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise LearningLoopError(f"{label} must be a JSON object")
    return value


def _exact_keys(
    value: Any, required: set[str], optional: set[str], label: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LearningLoopError(f"{label} must be an object")
    missing = sorted(required - value.keys())
    extra = sorted(value.keys() - required - optional)
    if missing:
        raise LearningLoopError(f"{label} missing fields: {', '.join(missing)}")
    if extra:
        raise LearningLoopError(f"{label} has unknown fields: {', '.join(extra)}")
    return value


def _nonblank(value: Any, label: str, *, minimum: int = 1) -> str:
    if not isinstance(value, str) or len(value.strip()) < minimum:
        raise LearningLoopError(f"{label} must be a non-empty explicit string")
    return value


def _string_list(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        qualifier = "non-empty " if nonempty else ""
        raise LearningLoopError(f"{label} must be a {qualifier}array")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise LearningLoopError(f"{label} must contain only non-empty strings")
    if len(set(value)) != len(value):
        raise LearningLoopError(f"{label} contains duplicates")
    return value


def _load_profile(
    workspace_path: Path, profile_path: Path
) -> tuple[Path, Path, dict[str, Any]]:
    try:
        return release_core._load_profile(workspace_path, profile_path)
    except release_core.ReleaseError as exc:
        raise LearningLoopError(str(exc)) from exc


def _pointer_and_bytes(
    workspace: Path,
    profile: dict[str, Any],
    raw_pointer: Any,
    label: str,
) -> tuple[dict[str, str], bytes]:
    try:
        pointer = release_core._validate_pointer(raw_pointer, label)
        content = release_core._state_bytes(
            workspace, profile, pointer["artifact_ref"]
        )
    except release_core.ReleaseError as exc:
        raise LearningLoopError(str(exc)) from exc
    assert content is not None
    actual = _sha256(content)
    if actual != pointer["artifact_sha256"]:
        raise LearningLoopError(
            f"{label} hash mismatch: expected {pointer['artifact_sha256']}, got {actual}"
        )
    return pointer, content


def _validate_schema(
    profile_path: Path, schema_id: str, value: dict[str, Any], label: str
) -> None:
    try:
        errors = validate_registered_instance(profile_path, schema_id, value)
    except SuiteConfigurationError as exc:
        raise LearningLoopError(f"cannot validate {label}: {exc}") from exc
    if errors:
        first = errors[0]
        raise LearningLoopError(
            f"{label} violates {schema_id} at {first.instance_path}: {first.message}"
        )


def _identity_matches(
    value: dict[str, Any], profile: dict[str, Any], label: str
) -> None:
    if value.get("project_id") != profile["project_id"]:
        raise LearningLoopError(f"cross-project {label} is forbidden")
    if value.get("project_profile_revision") != profile["profile_revision"]:
        raise LearningLoopError(f"{label} uses a stale project profile revision")


def _verify_nested_pointer(
    workspace: Path,
    profile: dict[str, Any],
    raw_pointer: Any,
    label: str,
) -> dict[str, str]:
    pointer, _ = _pointer_and_bytes(workspace, profile, raw_pointer, label)
    return pointer


def _validate_release_outcome(
    workspace: Path,
    profile_path: Path,
    profile: dict[str, Any],
    pointer: dict[str, str],
    value: dict[str, Any],
) -> dict[str, Any]:
    _validate_schema(profile_path, "release-outcome@1", value, "release outcome")
    _identity_matches(value, profile, "release outcome")
    if pointer["artifact_revision"] != value["outcome_id"]:
        raise LearningLoopError("release outcome revision does not match outcome_id")
    for field in ("release_package", "adapter_receipt", "live_proof"):
        _verify_nested_pointer(
            workspace, profile, value[field], f"release outcome {field}"
        )
    return {
        "proof_layer": "synthetic_fixture",
        "synthetic": True,
        "live": False,
        "recorded_at": value["recorded_at"],
        "causal_claim": False,
    }


def _validate_metric_source(
    workspace: Path,
    profile: dict[str, Any],
    value: dict[str, Any],
) -> dict[str, Any]:
    _exact_keys(
        value,
        {
            "outcome_artifact_version",
            "project_id",
            "project_profile_revision",
            "artifact_type",
            "proof_layer",
            "statement",
            "metrics",
            "causal_claim",
            "recorded_at",
        },
        set(),
        "outcome or metric artifact",
    )
    if value["outcome_artifact_version"] != "1.0":
        raise LearningLoopError("unsupported outcome artifact version")
    _identity_matches(value, profile, "outcome or metric artifact")
    if value["artifact_type"] not in {"outcome", "metric"}:
        raise LearningLoopError("artifact_type must be outcome or metric")
    proof_layer = value["proof_layer"]
    if proof_layer not in PROOF_LAYERS:
        raise LearningLoopError("outcome proof_layer is unsupported")
    _nonblank(value["statement"], "outcome statement", minimum=10)
    if not isinstance(value["metrics"], list):
        raise LearningLoopError("outcome metrics must be an array")
    if proof_layer in {"measured_non_live", "live_outcome"} and not value["metrics"]:
        raise LearningLoopError("a measured outcome requires at least one metric")
    if proof_layer == "not_measured" and value["metrics"]:
        raise LearningLoopError("not_measured outcomes cannot contain metric values")
    if not isinstance(value["causal_claim"], bool):
        raise LearningLoopError("outcome causal_claim must be boolean")
    if proof_layer in {"not_measured", "synthetic_fixture"} and value["causal_claim"]:
        raise LearningLoopError("unmeasured or synthetic outcomes cannot claim causality")
    _timestamp(value["recorded_at"], "outcome recorded_at")

    for index, metric in enumerate(value["metrics"]):
        _exact_keys(
            metric,
            {"metric_id", "value", "unit", "population", "window", "source_ref"},
            set(),
            f"metrics[{index}]",
        )
        _nonblank(metric["metric_id"], f"metrics[{index}].metric_id")
        if isinstance(metric["value"], bool) or not isinstance(
            metric["value"], (int, float, str)
        ):
            raise LearningLoopError(
                f"metrics[{index}].value must be an explicit number or string"
            )
        if isinstance(metric["value"], str) and not metric["value"].strip():
            raise LearningLoopError(f"metrics[{index}].value cannot be blank")
        for field in ("unit", "population", "window"):
            _nonblank(metric[field], f"metrics[{index}].{field}")
        _verify_nested_pointer(
            workspace,
            profile,
            metric["source_ref"],
            f"metrics[{index}].source_ref",
        )
    return {
        "proof_layer": proof_layer,
        "synthetic": proof_layer == "synthetic_fixture",
        "live": proof_layer == "live_outcome",
        "recorded_at": value["recorded_at"],
        "causal_claim": value["causal_claim"],
    }


def _timestamp(value: Any, label: str) -> Any:
    if not isinstance(value, str) or not RFC3339_UTC_RE.fullmatch(value):
        raise LearningLoopError(f"{label} must be RFC3339 UTC seconds")
    try:
        return project_memory._parse_rfc3339(value, label)
    except project_memory.ProjectMemoryError as exc:
        raise LearningLoopError(str(exc)) from exc


def _extract_operating_contract(
    content: bytes, profile: dict[str, Any]
) -> dict[str, Any]:
    value = _strict_json(content, "operating contract artifact")
    contract = value.get("operating_contract", value)
    if not isinstance(contract, dict):
        raise LearningLoopError("operating contract artifact has no contract object")
    if value is not contract:
        if "project_id" in value and value["project_id"] != profile["project_id"]:
            raise LearningLoopError("cross-project operating contract container is forbidden")
        if (
            "project_profile_revision" in value
            and value["project_profile_revision"] != profile["profile_revision"]
        ):
            raise LearningLoopError(
                "operating contract container uses a stale project profile revision"
            )
    _identity_matches(contract, profile, "operating contract")
    return contract


def _validate_evaluation(
    evaluation: Any,
    profile: dict[str, Any],
    contract: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    value = _exact_keys(
        evaluation,
        {
            "evaluation_version",
            "memory_id",
            "memory_revision",
            "source_run_id",
            "source_task_id",
            "learning_statement",
            "claim_mode",
            "evidence_grade",
            "decision_eligible",
            "causal_claim",
            "experiment_evidence",
            "confidence",
            "tags",
            "supersedes",
            "unknowns",
            "assumptions",
            "evaluation_basis",
            "fabricated_values",
            "outcome_disposition",
            "expires_at",
            "evaluated_at",
            "next_decision",
        },
        set(),
        "evaluation",
    )
    if value["evaluation_version"] != LOOP_VERSION:
        raise LearningLoopError("unsupported evaluation version")
    _nonblank(value["learning_statement"], "learning_statement", minimum=10)
    if value["claim_mode"] not in CLAIM_MODES:
        raise LearningLoopError("claim_mode is unsupported")
    if value["evidence_grade"] not in contract["data_policy"][
        "allowed_evidence_grades"
    ]:
        raise LearningLoopError("evidence_grade is not allowed by the operating contract")
    if not isinstance(value["decision_eligible"], bool):
        raise LearningLoopError("decision_eligible must be boolean")
    if not isinstance(value["causal_claim"], bool):
        raise LearningLoopError("causal_claim must be boolean")
    if value["confidence"] not in {"low", "medium", "high"}:
        raise LearningLoopError("confidence is unsupported")
    _string_list(value["tags"], "tags")
    _string_list(value["supersedes"], "supersedes")
    _string_list(value["unknowns"], "unknowns")
    _string_list(value["assumptions"], "assumptions")
    _string_list(value["evaluation_basis"], "evaluation_basis", nonempty=True)
    if value["fabricated_values"] is not False:
        raise LearningLoopError("fabricated_values must be explicitly false")
    if value["outcome_disposition"] not in DISPOSITIONS:
        raise LearningLoopError("outcome_disposition is unsupported")
    evaluated_at = _timestamp(value["evaluated_at"], "evaluated_at")
    if value["expires_at"] is not None:
        _timestamp(value["expires_at"], "expires_at")
    outcome_at = _timestamp(source["recorded_at"], "outcome recorded_at")
    contract_at = _timestamp(
        contract["created_at"], "operating contract created_at"
    )
    if outcome_at < contract_at:
        raise LearningLoopError("the exact outcome cannot precede its operating contract")
    if evaluated_at < outcome_at:
        raise LearningLoopError("evaluated_at cannot precede the exact outcome")
    if evaluated_at < contract_at:
        raise LearningLoopError("evaluated_at cannot precede the operating contract")

    if source["synthetic"]:
        if value["causal_claim"] or value["claim_mode"] == "experiment_result":
            raise LearningLoopError("synthetic fixture outcomes must remain noncausal")
        if value["decision_eligible"]:
            raise LearningLoopError(
                "synthetic fixture outcomes cannot become decision-eligible live memory"
            )
    if source["proof_layer"] == "not_measured" and value["claim_mode"] == "experiment_result":
        raise LearningLoopError("an unmeasured outcome cannot be an experiment result")
    if value["causal_claim"]:
        if value["claim_mode"] != "experiment_result":
            raise LearningLoopError("causal claims require claim_mode experiment_result")
        if value["evidence_grade"] != "verified" or not value["decision_eligible"]:
            raise LearningLoopError(
                "causal claims require verified, decision-eligible evidence"
            )
        if value["experiment_evidence"] is None:
            raise LearningLoopError("causal claims require exact experiment evidence")
    else:
        if value["claim_mode"] == "experiment_result":
            raise LearningLoopError("experiment_result must make an explicit causal claim")
        if value["experiment_evidence"] is not None:
            raise LearningLoopError(
                "noncausal evaluations cannot attach experiment evidence"
            )

    decision = _exact_keys(
        value["next_decision"],
        {
            "proposal_id",
            "proposal_revision",
            "decision_question",
            "rationale",
            "recommended_action",
            "success_signal",
            "stop_condition",
            "owner_role_id",
        },
        set(),
        "next_decision",
    )
    if not isinstance(decision["proposal_id"], str) or not PROPOSAL_ID_RE.fullmatch(
        decision["proposal_id"]
    ):
        raise LearningLoopError("next_decision.proposal_id is invalid")
    if not isinstance(decision["proposal_revision"], str) or not REVISION_RE.fullmatch(
        decision["proposal_revision"]
    ):
        raise LearningLoopError("next_decision.proposal_revision is invalid")
    for field in (
        "decision_question",
        "rationale",
        "recommended_action",
        "stop_condition",
    ):
        _nonblank(decision[field], f"next_decision.{field}", minimum=10)
    if not isinstance(decision["owner_role_id"], str) or not ROLE_ID_RE.fullmatch(
        decision["owner_role_id"]
    ):
        raise LearningLoopError("next_decision.owner_role_id is invalid")
    if decision["owner_role_id"] != contract["outcome_owner"]["role_id"]:
        raise LearningLoopError("next decision must retain the accountable outcome owner")
    signal = _exact_keys(
        decision["success_signal"],
        {"name", "population", "window", "decision_rule"},
        set(),
        "next_decision.success_signal",
    )
    for field in ("name", "population", "window", "decision_rule"):
        _nonblank(signal[field], f"next_decision.success_signal.{field}")
    return value


def _validate_experiment_evidence(
    workspace: Path,
    profile_path: Path,
    profile: dict[str, Any],
    raw_pointer: Any,
) -> dict[str, str]:
    pointer, content = _pointer_and_bytes(
        workspace, profile, raw_pointer, "experiment_evidence"
    )
    record = _strict_json(content, "experiment evidence")
    try:
        project_memory.validate_memory_record(
            profile_path, record, verify_evidence=True
        )
    except project_memory.ProjectMemoryError as exc:
        raise LearningLoopError(
            f"experiment evidence is not a validated project memory record: {exc}"
        ) from exc
    if (
        record["memory_type"] != "learning"
        or record["claim_mode"] != "experiment_result"
        or record["causal_claim"] is not True
        or record["evidence_grade"] != "verified"
        or record["decision_eligible"] is not True
    ):
        raise LearningLoopError(
            "experiment evidence must be a verified causal experiment-result memory"
        )
    if pointer["artifact_revision"] != record["memory_revision"]:
        raise LearningLoopError("experiment evidence revision does not match the record")
    if PurePosixPath(pointer["artifact_ref"]) != project_memory.memory_record_path(record):
        raise LearningLoopError("experiment evidence is not at its canonical memory path")
    return pointer


def _memory_record(
    profile: dict[str, Any],
    outcome: dict[str, str],
    contract_pointer: dict[str, str],
    evaluation: dict[str, Any],
    experiment: dict[str, str] | None,
) -> dict[str, Any]:
    evidence = [
        {**outcome, "relation": "supports"},
        {**contract_pointer, "relation": "records"},
    ]
    if experiment is not None:
        evidence.append({**experiment, "relation": "supports"})
    return {
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "record_version": "1.0",
        "memory_id": evaluation["memory_id"],
        "memory_revision": evaluation["memory_revision"],
        "memory_type": "learning",
        "claim_mode": evaluation["claim_mode"],
        "statement": evaluation["learning_statement"],
        "tags": evaluation["tags"],
        "evidence": evidence,
        "evidence_grade": evaluation["evidence_grade"],
        "decision_eligible": evaluation["decision_eligible"],
        "causal_claim": evaluation["causal_claim"],
        "experiment_ref": None if experiment is None else experiment["artifact_ref"],
        "supersedes": evaluation["supersedes"],
        "confidence": evaluation["confidence"],
        "source_run_id": evaluation["source_run_id"],
        "source_task_id": evaluation["source_task_id"],
        "created_by": "growth-clockwork-root",
        "contains_personal_data": False,
        "valid_from": evaluation["evaluated_at"],
        "expires_at": evaluation["expires_at"],
        "created_at": evaluation["evaluated_at"],
    }


def _proposal_path(evaluation: dict[str, Any]) -> PurePosixPath:
    decision = evaluation["next_decision"]
    return PurePosixPath(
        "records",
        "next-decisions",
        decision["proposal_id"],
        f"{decision['proposal_revision']}.json",
    )


def _next_decision(
    profile: dict[str, Any],
    outcome: dict[str, str],
    contract_pointer: dict[str, str],
    memory_pointer: dict[str, str],
    evaluation: dict[str, Any],
    source: dict[str, Any],
    experiment: dict[str, str] | None,
) -> dict[str, Any]:
    decision = evaluation["next_decision"]
    evidence = [outcome, contract_pointer]
    if experiment is not None:
        evidence.append(experiment)
    return {
        "proposal_version": "1.0",
        "proposal_id": decision["proposal_id"],
        "proposal_revision": decision["proposal_revision"],
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "source_run_id": evaluation["source_run_id"],
        "source_task_id": evaluation["source_task_id"],
        "source_outcome": outcome,
        "source_operating_contract": contract_pointer,
        "learning_record": memory_pointer,
        "experiment_evidence": experiment,
        "proof_layer": source["proof_layer"],
        "source_live": source["live"],
        "source_synthetic": source["synthetic"],
        "outcome_disposition": evaluation["outcome_disposition"],
        "decision_question": decision["decision_question"],
        "rationale": decision["rationale"],
        "recommended_action": decision["recommended_action"],
        "success_signal": decision["success_signal"],
        "stop_condition": decision["stop_condition"],
        "owner_role_id": decision["owner_role_id"],
        "unknowns": evaluation["unknowns"],
        "assumptions": evaluation["assumptions"],
        "evaluation_basis": evaluation["evaluation_basis"],
        "fabricated_values": False,
        "causal_claim": evaluation["causal_claim"],
        "status": "proposed",
        "may_execute": False,
        "may_publish": False,
        "external_side_effects": False,
        "created_at": evaluation["evaluated_at"],
    }


def close_learning_loop(
    workspace_path: Path,
    profile_path: Path,
    *,
    outcome: dict[str, str],
    outcome_kind: str,
    operating_contract: dict[str, str],
    evaluation: dict[str, Any],
) -> dict[str, Any]:
    """Persist one memory record and one non-executable decision proposal."""

    workspace, resolved_profile, profile = _load_profile(
        workspace_path, profile_path
    )
    if outcome_kind not in SOURCE_KINDS:
        raise LearningLoopError("outcome_kind is unsupported")
    outcome_pointer, outcome_content = _pointer_and_bytes(
        workspace, profile, outcome, "outcome"
    )
    outcome_value = _strict_json(outcome_content, "outcome")
    if outcome_kind == RELEASE_OUTCOME:
        source = _validate_release_outcome(
            workspace,
            resolved_profile,
            profile,
            outcome_pointer,
            outcome_value,
        )
    else:
        source = _validate_metric_source(workspace, profile, outcome_value)

    contract_pointer, contract_content = _pointer_and_bytes(
        workspace, profile, operating_contract, "operating_contract"
    )
    contract = _extract_operating_contract(contract_content, profile)
    try:
        validate_operating_contract(resolved_profile, contract)
    except OperatingContractError as exc:
        raise LearningLoopError(str(exc)) from exc
    destinations = set(contract["data_policy"]["permitted_destinations"])
    if not {"memory", "records"}.issubset(destinations):
        raise LearningLoopError(
            "operating contract must permit both memory and records destinations"
        )
    if "write_internal_artifacts" not in contract["authority"]["allowed_actions"]:
        raise LearningLoopError(
            "operating contract does not allow internal artifact writes"
        )
    if source["live"] and "live_outcome" not in contract["proof_requirements"]:
        raise LearningLoopError(
            "a live outcome requires live_outcome proof in the operating contract"
        )

    checked_evaluation = _validate_evaluation(
        evaluation, profile, contract, source
    )
    experiment_pointer = None
    if checked_evaluation["experiment_evidence"] is not None:
        experiment_pointer = _validate_experiment_evidence(
            workspace,
            resolved_profile,
            profile,
            checked_evaluation["experiment_evidence"],
        )

    record = _memory_record(
        profile,
        outcome_pointer,
        contract_pointer,
        checked_evaluation,
        experiment_pointer,
    )
    try:
        project_memory.validate_memory_record(
            resolved_profile, record, verify_evidence=True
        )
    except project_memory.ProjectMemoryError as exc:
        raise LearningLoopError(str(exc)) from exc
    record_content = _canonical_json(record)
    record_ref = project_memory.memory_record_path(record).as_posix()
    record_pointer = release_core._pointer(
        record_ref, record["memory_revision"], record_content
    )
    proposal = _next_decision(
        profile,
        outcome_pointer,
        contract_pointer,
        record_pointer,
        checked_evaluation,
        source,
        experiment_pointer,
    )
    proposal_content = _canonical_json(proposal)
    proposal_ref = _proposal_path(checked_evaluation).as_posix()
    proposal_pointer = release_core._pointer(
        proposal_ref,
        proposal["proposal_revision"],
        proposal_content,
    )

    identity = _canonical_json(
        {
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
            "outcome": outcome_pointer,
            "operating_contract": contract_pointer,
            "evaluation": checked_evaluation,
        }
    )
    identity_hash = _sha256(identity)
    idempotency_key = str(uuid.uuid5(REQUEST_NAMESPACE, identity_hash))
    writer_run_id = f"learning-{identity_hash[:24]}"
    try:
        request = project_memory.build_memory_write_request(
            resolved_profile,
            record,
            run_id=writer_run_id,
            idempotency_key=idempotency_key,
        )
    except project_memory.ProjectMemoryError as exc:
        raise LearningLoopError(str(exc)) from exc
    request["writes"].append(
        {
            "path": proposal_ref,
            "mode": "create",
            "content": proposal_content.decode("utf-8"),
            "content_sha256": proposal_pointer["artifact_sha256"],
            "expected_sha256": None,
            "media_type": "application/json",
        }
    )

    with tempfile.TemporaryDirectory(prefix="growth-learning-loop-") as temporary:
        request_path = Path(temporary) / "request.json"
        request_path.write_bytes(_canonical_json(request))
        try:
            receipt = root_writer.apply_request(
                workspace, resolved_profile, request_path
            )
        except root_writer.WriterError as exc:
            raise LearningLoopError(str(exc)) from exc
    expected = {
        record_ref: record_pointer["artifact_sha256"],
        proposal_ref: proposal_pointer["artifact_sha256"],
    }
    actual = {item["path"]: item["sha256"] for item in receipt["writes"]}
    if actual != expected:
        raise LearningLoopError(
            "Root Writer receipt does not cover the exact two-output transaction"
        )
    return {
        "status": "completed",
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "memory_record": record_pointer,
        "next_decision": proposal_pointer,
        "writer_receipt": receipt,
        "external_side_effects": False,
        "publicly_live": False,
    }


def _load_evaluation(path: Path) -> dict[str, Any]:
    try:
        return _strict_json(path.read_bytes(), "evaluation")
    except OSError as exc:
        raise LearningLoopError(f"cannot read evaluation: {path}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--project-config", required=True, type=Path)
    parser.add_argument("--outcome-ref", required=True)
    parser.add_argument("--outcome-revision", required=True)
    parser.add_argument("--outcome-sha256", required=True)
    parser.add_argument("--outcome-kind", required=True, choices=sorted(SOURCE_KINDS))
    parser.add_argument("--operating-contract-ref", required=True)
    parser.add_argument("--operating-contract-revision", required=True)
    parser.add_argument("--operating-contract-sha256", required=True)
    parser.add_argument("--evaluation", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = close_learning_loop(
            args.workspace,
            args.project_config,
            outcome={
                "artifact_ref": args.outcome_ref,
                "artifact_revision": args.outcome_revision,
                "artifact_sha256": args.outcome_sha256,
            },
            outcome_kind=args.outcome_kind,
            operating_contract={
                "artifact_ref": args.operating_contract_ref,
                "artifact_revision": args.operating_contract_revision,
                "artifact_sha256": args.operating_contract_sha256,
            },
            evaluation=_load_evaluation(args.evaluation),
        )
    except LearningLoopError as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
