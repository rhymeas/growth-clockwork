#!/usr/bin/env python3
"""Prepare one project-bound full-route rework run from an exact human note.

This adapter verifies an immutable ``note`` action, its exact review lineage,
the source run manifest chain, and every relevant Root Writer receipt. It then
materializes a new initial route wave with the same pinned context and byte-
identical route/role contracts. It performs no model call, shell command,
network request, publication, or external side effect.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
from typing import Any, Iterable
import uuid

from pipeline.agent_registry import AgentRegistryError, load_agent_registry
from pipeline import root_writer, run_engine
from pipeline.validate_json_suites import (
    SuiteConfigurationError,
    ValidationError,
    validate_registered_instance,
)


REWORK_RUN_NAMESPACE = uuid.UUID("9cdd7b88-5f68-4aa7-b35b-a0baa446973e")
REWORK_REQUEST_NAMESPACE = uuid.UUID("b662bd5e-36ae-4ad1-93b5-616f84dc4068")
REWORK_WRITER_NAMESPACE = uuid.UUID("a7a857b3-bc30-4aae-a97a-f61b5d2415d9")
REVISION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ReworkError(ValueError):
    """Raised before a rework run may become dispatchable."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json(value: Any) -> bytes:
    try:
        return (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError) as exc:
        raise ReworkError("rework data must be canonical UTF-8 JSON") from exc


def _json_object(content: bytes, label: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-JSON numeric constant {value}")

    try:
        value = json.loads(content.decode("utf-8"), parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReworkError(f"cannot parse {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReworkError(f"{label} must be a JSON object")
    return value


def _exact_keys(value: Any, required: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReworkError(f"{label} must be a JSON object")
    missing = sorted(required - value.keys())
    extra = sorted(value.keys() - required)
    if missing:
        raise ReworkError(f"{label} missing fields: {', '.join(missing)}")
    if extra:
        raise ReworkError(f"{label} has unknown fields: {', '.join(extra)}")
    return value


def _format_errors(errors: Iterable[ValidationError]) -> str:
    return "; ".join(
        f"{error.instance_path} [{error.rule}]: {error.message}"
        for error in errors
    )


def _validate_instance(
    profile_path: Path, schema_id: str, value: dict[str, Any], label: str
) -> None:
    try:
        errors = validate_registered_instance(profile_path, schema_id, value)
    except SuiteConfigurationError as exc:
        raise ReworkError(str(exc)) from exc
    if errors:
        raise ReworkError(f"{label} failed {schema_id}: {_format_errors(errors)}")


def _pointer(ref: str, revision: str, content: bytes) -> dict[str, str]:
    return {
        "artifact_ref": ref,
        "artifact_revision": revision,
        "artifact_sha256": _sha256(content),
    }


def _normalized_pointer(pointer: Any, label: str) -> dict[str, str]:
    if not isinstance(pointer, dict) or set(pointer) != {
        "artifact_ref",
        "artifact_revision",
        "artifact_sha256",
    }:
        raise ReworkError(
            f"{label} must contain artifact_ref, artifact_revision, and artifact_sha256"
        )
    try:
        relative = root_writer._validate_relative_path(
            pointer["artifact_ref"], f"{label}.artifact_ref"
        )
    except root_writer.WriterError as exc:
        raise ReworkError(str(exc)) from exc
    revision = pointer["artifact_revision"]
    digest = pointer["artifact_sha256"]
    if not isinstance(revision, str) or not REVISION_RE.fullmatch(revision):
        raise ReworkError(f"{label}.artifact_revision is invalid")
    if not isinstance(digest, str) or not root_writer.SHA256_RE.fullmatch(digest):
        raise ReworkError(f"{label}.artifact_sha256 is invalid")
    return {
        "artifact_ref": relative.as_posix(),
        "artifact_revision": revision,
        "artifact_sha256": digest,
    }


def _write_item(
    path: str, content: bytes, media_type: str = "application/json"
) -> dict[str, Any]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReworkError(f"Root Writer input is not UTF-8: {path}") from exc
    return {
        "path": path,
        "mode": "create",
        "content": text,
        "content_sha256": _sha256(content),
        "expected_sha256": None,
        "media_type": media_type,
    }


def _state_bytes(
    workspace: Path,
    profile: dict[str, Any],
    raw_ref: str,
    *,
    missing_ok: bool = False,
) -> bytes | None:
    try:
        relative = root_writer._validate_relative_path(raw_ref, "state artifact_ref")
        with root_writer._project_state_fd(
            workspace, profile["_state_root"]
        ) as state_fd:
            return root_writer._read_relative_bytes(
                state_fd, relative, missing_ok=missing_ok
            )
    except root_writer.WriterError as exc:
        raise ReworkError(str(exc)) from exc


def _pinned_bytes(
    workspace: Path,
    profile: dict[str, Any],
    pointer: dict[str, Any],
    label: str,
) -> bytes:
    normalized = _normalized_pointer(pointer, label)
    content = _state_bytes(workspace, profile, normalized["artifact_ref"])
    assert content is not None
    if _sha256(content) != normalized["artifact_sha256"]:
        raise ReworkError(f"{label} hash does not match persisted bytes")
    return content


def _receipt_covering(
    workspace: Path,
    profile: dict[str, Any],
    run_id: str,
    artifact_ref: str,
    content: bytes,
) -> None:
    try:
        run_engine._receipt_covering(
            workspace, profile, run_id, artifact_ref, content
        )
    except run_engine.RunEngineError as exc:
        raise ReworkError(str(exc)) from exc


def _context_receipt_covering(
    workspace: Path, profile: dict[str, Any], run_id: str,
    artifact_ref: str, content: bytes,
) -> None:
    """A rework context keeps its original same-project receipt and bytes."""
    try:
        run_engine._receipt_covering_project_state(
            workspace, profile, run_id, artifact_ref, content
        )
    except run_engine.RunEngineError as exc:
        raise ReworkError(str(exc)) from exc


def _next_revision(previous: str, action_id: str) -> str:
    numeric = re.fullmatch(r"r([0-9]+)", previous)
    if numeric:
        return f"r{int(numeric.group(1)) + 1}"
    suffix = action_id.replace("-", "")[:12]
    candidate = f"{previous}-rework-{suffix}"
    if len(candidate) <= 128:
        return candidate
    return f"rework-{suffix}"


def _action_and_pointer(
    workspace: Path,
    profile_path: Path,
    profile: dict[str, Any],
    action_ref: str,
) -> tuple[dict[str, Any], dict[str, str], bytes]:
    try:
        relative = root_writer._validate_relative_path(action_ref, "action_ref")
    except root_writer.WriterError as exc:
        raise ReworkError(str(exc)) from exc
    content = _state_bytes(workspace, profile, relative.as_posix())
    assert content is not None
    action = _json_object(content, "human review action")
    if (
        action.get("project_id") != profile["project_id"]
        or action.get("project_profile_revision") != profile["profile_revision"]
    ):
        raise ReworkError("human review action belongs to another project profile")
    _validate_instance(
        profile_path, "human-review-action@1", action, "human review action"
    )
    if action["action"] != "note" or action["rework_requested"] is not True:
        raise ReworkError("only a human note action may prepare rework")
    try:
        parsed_action_id = uuid.UUID(action["action_id"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise ReworkError("human review action_id is invalid") from exc
    if str(parsed_action_id) != action["action_id"]:
        raise ReworkError("human review action_id is not canonical")
    canonical = (
        PurePosixPath("outbox/review-actions")
        / action["artifact_id"]
        / f"{action['artifact_revision']}.json"
    )
    if relative != canonical:
        raise ReworkError("human review action path does not match its identity")
    review_run_id = f"review-{action['action_id'].replace('-', '')[:20]}"
    _receipt_covering(workspace, profile, review_run_id, canonical.as_posix(), content)
    return action, _pointer(canonical.as_posix(), action["action_id"], content), content


def _lineage(
    workspace: Path,
    profile: dict[str, Any],
    action: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str], bytes]:
    ref = (
        PurePosixPath("outbox/lineage")
        / action["artifact_id"]
        / f"{action['artifact_revision']}.json"
    ).as_posix()
    content = _state_bytes(workspace, profile, ref)
    assert content is not None
    value = _exact_keys(
        _json_object(content, "review lineage"),
        {
            "lineage_version",
            "project_id",
            "project_profile_revision",
            "artifact_id",
            "artifact_revision",
            "artifact_sha256",
            "source_run_id",
            "source_route_id",
            "producer",
            "governance",
            "review_manifest_ref",
        },
        "review lineage",
    )
    if value["lineage_version"] != "1.0":
        raise ReworkError("unsupported review lineage version")
    expected_identity = (
        profile["project_id"],
        profile["profile_revision"],
        action["artifact_id"],
        action["artifact_revision"],
        action["artifact_sha256"],
    )
    actual_identity = (
        value["project_id"],
        value["project_profile_revision"],
        value["artifact_id"],
        value["artifact_revision"],
        value["artifact_sha256"],
    )
    if actual_identity != expected_identity:
        raise ReworkError("review lineage does not match the exact human action")
    if not isinstance(value["source_run_id"], str) or not root_writer.RUN_ID_RE.fullmatch(
        value["source_run_id"]
    ):
        raise ReworkError("review lineage source_run_id is invalid")
    if not isinstance(value["source_route_id"], str) or not root_writer.RUN_ID_RE.fullmatch(
        value["source_route_id"]
    ):
        raise ReworkError("review lineage source_route_id is invalid")
    producer = _exact_keys(
        value["producer"],
        {"step_id", "task_ref", "result_ref", "artifact_ref"},
        "review lineage producer",
    )
    governance = _exact_keys(
        value["governance"],
        {"step_id", "task_ref", "result_ref"},
        "review lineage governance",
    )
    for label, pointer in (
        ("producer.task_ref", producer["task_ref"]),
        ("producer.result_ref", producer["result_ref"]),
        ("producer.artifact_ref", producer["artifact_ref"]),
        ("governance.task_ref", governance["task_ref"]),
        ("governance.result_ref", governance["result_ref"]),
        ("review_manifest_ref", value["review_manifest_ref"]),
    ):
        _normalized_pointer(pointer, f"review lineage {label}")
    _receipt_covering(workspace, profile, value["source_run_id"], ref, content)
    return value, _pointer(ref, action["artifact_revision"], content), content


def _source_run(
    workspace: Path,
    profile_path: Path,
    profile: dict[str, Any],
    registry: Any,
    action: dict[str, Any],
    lineage: dict[str, Any],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    Any,
    bytes,
    dict[str, dict[str, str]],
]:
    run_id = lineage["source_run_id"]
    try:
        manifest, _, _ = run_engine._latest_manifest(
            workspace, profile_path, profile, run_id
        )
        route = run_engine._route_for_manifest(workspace, profile, registry, manifest)
        source_plan = run_engine._plan_for_manifest(
            workspace, profile_path, profile, manifest
        )
    except run_engine.RunEngineError as exc:
        raise ReworkError(str(exc)) from exc
    if manifest["status"] != "awaiting_review":
        raise ReworkError("source run is not awaiting exact human review")
    if manifest["route_id"] != lineage["source_route_id"]:
        raise ReworkError("source run route does not match review lineage")
    if lineage["review_manifest_ref"] not in manifest["review_items"]:
        raise ReworkError("source run does not contain the lineage review manifest")
    if any(task["status"] != "completed" for task in manifest["tasks"]):
        raise ReworkError("source run has a non-completed route task")

    plan_bytes = _pinned_bytes(
        workspace, profile, manifest["coordinator_plan"], "source coordinator plan"
    )
    _receipt_covering(
        workspace,
        profile,
        run_id,
        manifest["coordinator_plan"]["artifact_ref"],
        plan_bytes,
    )
    context_bytes = _pinned_bytes(
        workspace, profile, manifest["project_context"], "source project context"
    )
    _context_receipt_covering(
        workspace,
        profile,
        run_id,
        manifest["project_context"]["artifact_ref"],
        context_bytes,
    )
    context_manifest = _json_object(context_bytes, "source project context")
    _validate_instance(
        profile_path,
        "project-context-manifest@1",
        context_manifest,
        "source project context",
    )
    if (
        context_manifest["project_id"] != profile["project_id"]
        or context_manifest["project_profile_revision"] != profile["profile_revision"]
        or context_manifest["context_revision"]
        != manifest["project_context"]["artifact_revision"]
    ):
        raise ReworkError("source project context identity does not match the run")
    for section_entry in context_manifest["sections"]:
        section_pointer = {
            "artifact_ref": section_entry["artifact_ref"],
            "artifact_revision": section_entry["artifact_revision"],
            "artifact_sha256": section_entry["artifact_sha256"],
        }
        section_bytes = _pinned_bytes(
            workspace,
            profile,
            section_pointer,
            f"source context section {section_entry['kind']}",
        )
        _context_receipt_covering(
            workspace,
            profile,
            run_id,
            section_pointer["artifact_ref"],
            section_bytes,
        )
        section = _json_object(
            section_bytes, f"source context section {section_entry['kind']}"
        )
        _validate_instance(
            profile_path,
            "context-section-envelope@1",
            section,
            f"source context section {section_entry['kind']}",
        )
        if (
            section["project_id"] != profile["project_id"]
            or section["project_profile_revision"] != profile["profile_revision"]
            or section["section_kind"] != section_entry["kind"]
            or section["section_revision"] != section_entry["artifact_revision"]
        ):
            raise ReworkError("source context section identity does not match its pin")
        for source in section["source_refs"]:
            source_pointer = {
                "artifact_ref": source["artifact_ref"],
                "artifact_revision": source["artifact_revision"],
                "artifact_sha256": source["artifact_sha256"],
            }
            source_bytes = _pinned_bytes(
                workspace,
                profile,
                source_pointer,
                f"source context evidence {source['source_id']}",
            )
            _context_receipt_covering(
                workspace,
                profile,
                run_id,
                source_pointer["artifact_ref"],
                source_bytes,
            )
    route_bytes = _pinned_bytes(
        workspace, profile, manifest["route_contract"], "source route contract"
    )
    _receipt_covering(
        workspace,
        profile,
        run_id,
        manifest["route_contract"]["artifact_ref"],
        route_bytes,
    )
    if route_bytes != route.artifact.content:
        raise ReworkError("source route contract differs from the profile-pinned route")

    registry_ref = f"records/run-inputs/{run_id}/agents/registry.json"
    registry_bytes = _state_bytes(workspace, profile, registry_ref)
    assert registry_bytes is not None
    if registry_bytes != registry.artifact.content:
        raise ReworkError("source agent registry snapshot differs from the selected profile")
    _receipt_covering(workspace, profile, run_id, registry_ref, registry_bytes)

    tasks_by_step = {item["step_id"]: item for item in manifest["tasks"]}
    role_pointers: dict[str, dict[str, str]] = {}
    for role_id in sorted({node.role_id for node in route.nodes}):
        role_ref = f"records/run-inputs/{run_id}/agents/roles/{role_id}.json"
        role_bytes = _state_bytes(workspace, profile, role_ref)
        assert role_bytes is not None
        if role_bytes != registry.roles[role_id].artifact.content:
            raise ReworkError(f"source role snapshot differs from pinned role: {role_id}")
        _receipt_covering(workspace, profile, run_id, role_ref, role_bytes)
        role_pointers[role_id] = _pointer(
            role_ref, registry.roles[role_id].artifact.revision, role_bytes
        )

    results_by_step: dict[str, dict[str, Any]] = {}
    for entry in manifest["tasks"]:
        try:
            task, _ = run_engine._load_task(
                workspace, profile_path, profile, manifest, entry
            )
            if entry["result_ref"] is None:
                raise ReworkError("source task has no terminal result")
            result, _ = run_engine._load_result(
                workspace, profile_path, profile, run_id, entry["result_ref"]
            )
        except run_engine.RunEngineError as exc:
            raise ReworkError(str(exc)) from exc
        if task["role_contract"] != role_pointers[task["assigned_role"]]:
            raise ReworkError("source task does not use its pinned role contract")
        results_by_step[entry["step_id"]] = result

    producer_entry = tasks_by_step.get(route.final_product_node)
    governance_entry = tasks_by_step.get(route.governance_node)
    if producer_entry is None or governance_entry is None:
        raise ReworkError("source run is missing terminal route tasks")
    expected_producer_task = {
        "artifact_ref": producer_entry["task_ref"],
        "artifact_revision": producer_entry["task_id"],
        "artifact_sha256": producer_entry["task_sha256"],
    }
    expected_governance_task = {
        "artifact_ref": governance_entry["task_ref"],
        "artifact_revision": governance_entry["task_id"],
        "artifact_sha256": governance_entry["task_sha256"],
    }
    if (
        lineage["producer"]["step_id"] != route.final_product_node
        or lineage["producer"]["task_ref"] != expected_producer_task
        or lineage["producer"]["result_ref"] != producer_entry["result_ref"]
        or lineage["governance"]["step_id"] != route.governance_node
        or lineage["governance"]["task_ref"] != expected_governance_task
        or lineage["governance"]["result_ref"] != governance_entry["result_ref"]
    ):
        raise ReworkError("review lineage terminal tasks do not match the source run")
    producer_pointer = lineage["producer"]["artifact_ref"]
    if (
        producer_pointer["artifact_revision"] != action["artifact_revision"]
        or producer_pointer["artifact_sha256"] != action["artifact_sha256"]
    ):
        raise ReworkError("review lineage producer does not match the human action")
    producer_matches = [
        output
        for output in results_by_step[route.final_product_node]["output_artifacts"]
        if output["artifact_ref"] == producer_pointer["artifact_ref"]
        and output["artifact_revision"] == producer_pointer["artifact_revision"]
        and output["artifact_sha256"] == producer_pointer["artifact_sha256"]
        and output["contract_id"] == route.final_product_contract
    ]
    if len(producer_matches) != 1:
        raise ReworkError(
            "review lineage producer is not the source route's exact final output"
        )
    producer_bytes = _pinned_bytes(
        workspace, profile, producer_pointer, "source producer artifact"
    )
    _receipt_covering(
        workspace, profile, run_id, producer_pointer["artifact_ref"], producer_bytes
    )
    return manifest, source_plan, route, registry_bytes, role_pointers


def _reviewed_artifact(
    workspace: Path,
    profile_path: Path,
    profile: dict[str, Any],
    action: dict[str, Any],
    lineage: dict[str, Any],
) -> tuple[dict[str, str], bytes]:
    review_pointer = lineage["review_manifest_ref"]
    review_content = _pinned_bytes(
        workspace, profile, review_pointer, "lineage review manifest"
    )
    review = _json_object(review_content, "lineage review manifest")
    _validate_instance(
        profile_path, "outbox-review-item@1", review, "lineage review manifest"
    )
    canonical_review_ref = (
        PurePosixPath("outbox/pending")
        / action["artifact_id"]
        / f"{action['artifact_revision']}.json"
    ).as_posix()
    if review_pointer["artifact_ref"] != canonical_review_ref:
        raise ReworkError("review manifest path does not match the human action")
    if (
        review["project_id"] != action["project_id"]
        or review["project_profile_revision"] != action["project_profile_revision"]
        or review["artifact_id"] != action["artifact_id"]
        or review["artifact_revision"] != action["artifact_revision"]
        or review["artifact_sha256"] != action["artifact_sha256"]
    ):
        raise ReworkError("review manifest does not match the human action")
    source_run_id = lineage["source_run_id"]
    _receipt_covering(
        workspace, profile, source_run_id, canonical_review_ref, review_content
    )
    artifact_ref = review["artifact_path"]
    artifact_content = _state_bytes(workspace, profile, artifact_ref)
    assert artifact_content is not None
    if _sha256(artifact_content) != action["artifact_sha256"]:
        raise ReworkError("reviewed artifact bytes do not match the human action hash")
    producer_content = _pinned_bytes(
        workspace,
        profile,
        lineage["producer"]["artifact_ref"],
        "lineage producer artifact",
    )
    if producer_content != artifact_content:
        raise ReworkError("reviewed artifact differs from its exact producer output")
    _receipt_covering(
        workspace, profile, source_run_id, artifact_ref, artifact_content
    )
    return (
        {
            "artifact_ref": artifact_ref,
            "artifact_revision": action["artifact_revision"],
            "artifact_sha256": action["artifact_sha256"],
        },
        artifact_content,
    )


def _apply_request(
    workspace: Path,
    profile_path: Path,
    profile: dict[str, Any],
    run_id: str,
    writer_key: str,
    preconditions: list[dict[str, Any]],
    writes: list[dict[str, Any]],
) -> dict[str, Any]:
    request = {
        "writer_request_version": root_writer.REQUEST_VERSION,
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "run_id": run_id,
        "idempotency_key": writer_key,
        "requested_by": profile["root_role_id"],
        "preconditions": preconditions,
        "writes": writes,
    }
    request_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix="growth-rework-", suffix=".json", delete=False
        ) as handle:
            handle.write(_canonical_json(request))
            handle.flush()
            request_path = Path(handle.name)
        return root_writer.apply_request(workspace, profile_path, request_path)
    except (OSError, root_writer.WriterError) as exc:
        raise ReworkError(str(exc)) from exc
    finally:
        if request_path is not None:
            request_path.unlink(missing_ok=True)


def prepare_rework(
    workspace: Path,
    profile_path: Path,
    action_ref: str,
) -> dict[str, Any]:
    """Verify one note action and atomically prepare its deterministic rework run."""

    try:
        workspace = root_writer._validate_workspace(workspace)
        profile = root_writer.load_project_profile(profile_path)
        root_writer._validate_profile_package(workspace, profile_path, profile)
    except root_writer.WriterError as exc:
        raise ReworkError(str(exc)) from exc
    if "_agent_registry" not in profile:
        raise ReworkError("project profile has no pinned agent registry")
    try:
        registry = load_agent_registry(
            workspace, profile_path, profile["_agent_registry"]
        )
    except AgentRegistryError as exc:
        raise ReworkError(str(exc)) from exc

    action, action_pointer, _ = _action_and_pointer(
        workspace, profile_path, profile, action_ref
    )
    lineage, lineage_pointer, _ = _lineage(workspace, profile, action)
    source_manifest, source_plan, route, registry_bytes, source_role_pointers = (
        _source_run(
            workspace, profile_path, profile, registry, action, lineage
        )
    )
    previous_artifact, _ = _reviewed_artifact(
        workspace, profile_path, profile, action, lineage
    )
    expected_revision = _next_revision(
        action["artifact_revision"], action["action_id"]
    )
    next_action_ref = (
        PurePosixPath("outbox/review-actions")
        / action["artifact_id"]
        / f"{expected_revision}.json"
    ).as_posix()

    stable_identity = "\n".join(
        [
            profile["project_id"],
            profile["profile_revision"],
            action["action_id"],
            lineage_pointer["artifact_sha256"],
            expected_revision,
        ]
    )
    request_id = str(uuid.uuid5(REWORK_REQUEST_NAMESPACE, stable_identity))
    run_id = f"RUN-{uuid.uuid5(REWORK_RUN_NAMESPACE, stable_identity).hex[:16]}"
    writer_key = str(uuid.uuid5(REWORK_WRITER_NAMESPACE, stable_identity))
    objective = (
        f"Rework exact artifact {action['artifact_id']}@{action['artifact_revision']} "
        f"into new revision {expected_revision} from the exact human note. Preserve "
        "the reviewed revision unchanged and run the complete original route again."
    )
    plan = {
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "plan_version": "1.0",
        "request_id": request_id,
        "work_item_id": source_plan["work_item_id"],
        "run_mode": source_manifest["run_mode"],
        "objective": objective,
        "project_context": source_manifest["project_context"],
        "diagnosis": {
            "problem": "The exact reviewed artifact has a human note requesting a new immutable revision.",
            "controllable_bottleneck": "The requested change must traverse the same complete route with exact prior lineage.",
            "evidence_refs": [action_pointer, lineage_pointer, previous_artifact],
            "assumptions": [
                "Only the recorded human note defines the requested rework.",
                "The previous reviewed artifact remains immutable and is input evidence only.",
            ],
            "confidence": "high",
        },
        "route_decision": {
            "selected_route_id": route.route_id,
            "rationale": "Reusing the complete source route preserves its professional and governance checks.",
            "alternatives": source_plan["route_decision"]["alternatives"],
        },
        "requested_at": action["recorded_at"],
    }
    if "operating_contract" in source_plan:
        plan["operating_contract"] = json.loads(json.dumps(source_plan["operating_contract"]))
        plan["operating_contract"]["created_at"] = action["recorded_at"]
    if "step_activation" in source_plan["route_decision"]:
        plan["route_decision"]["step_activation"] = source_plan["route_decision"]["step_activation"]
    _validate_instance(profile_path, "coordinator-plan@1", plan, "rework plan")
    plan_content = _canonical_json(plan)
    plan_ref = f"records/coordinator-plans/{request_id}.json"
    plan_pointer = _pointer(plan_ref, request_id, plan_content)
    writes: list[dict[str, Any]] = [_write_item(plan_ref, plan_content)]

    source_run_id = source_manifest["run_id"]
    source_registry_ref = f"records/run-inputs/{source_run_id}/agents/registry.json"
    registry_ref = f"records/run-inputs/{run_id}/agents/registry.json"
    writes.append(_write_item(registry_ref, registry_bytes))
    if _state_bytes(workspace, profile, source_registry_ref) != registry_bytes:
        raise ReworkError("source registry changed during rework preparation")

    source_route_bytes = _pinned_bytes(
        workspace, profile, source_manifest["route_contract"], "source route contract"
    )
    route_ref = f"records/run-inputs/{run_id}/agents/route.json"
    route_pointer = _pointer(
        route_ref,
        source_manifest["route_contract"]["artifact_revision"],
        source_route_bytes,
    )
    writes.append(_write_item(route_ref, source_route_bytes))

    role_pointers: dict[str, dict[str, str]] = {}
    for role_id in sorted({node.role_id for node in route.nodes}):
        source_pointer = source_role_pointers[role_id]
        role_bytes = _pinned_bytes(
            workspace, profile, source_pointer, f"source role {role_id}"
        )
        role_ref = f"records/run-inputs/{run_id}/agents/roles/{role_id}.json"
        pointer = _pointer(
            role_ref, source_pointer["artifact_revision"], role_bytes
        )
        writes.append(_write_item(role_ref, role_bytes))
        role_pointers[role_id] = pointer

    task_ids = {
        node.node_id: f"{source_plan['work_item_id']}-T{index:03d}"
        for index, node in enumerate(route.nodes, start=1)
    }
    pattern = re.compile(profile["task_id_pattern"])
    if any(not pattern.fullmatch(task_id) for task_id in task_ids.values()):
        raise ReworkError("rework task_id does not match the selected profile")
    tasks: list[dict[str, Any]] = []
    rework_instruction = (
        f"Human rework note: {action['note']}\n"
        f"Expected next artifact revision: {expected_revision}.\n"
        f"Do not edit or replace {action['artifact_id']}@{action['artifact_revision']}; "
        "produce a new immutable revision and preserve all prior evidence."
    )
    for node in route.nodes:
        if node.depends_on:
            continue
        task_id = task_ids[node.node_id]
        inputs = [
            plan_pointer,
            source_manifest["project_context"],
            route_pointer,
            role_pointers[node.role_id],
            action_pointer,
            lineage_pointer,
            previous_artifact,
        ]
        if len(
            {
                (item["artifact_ref"], item["artifact_revision"], item["artifact_sha256"])
                for item in inputs
            }
        ) != len(inputs):
            raise ReworkError("rework initial task contains duplicate exact inputs")
        task = {
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
            "task_version": "2.0",
            "task_id": task_id,
            "run_id": run_id,
            "step_id": node.node_id,
            "depends_on": [],
            "assigned_role": node.role_id,
            "role_contract": role_pointers[node.role_id],
            "route_id": route.route_id,
            "route_contract": route_pointer,
            "objective": f"{objective}\n\n{rework_instruction}\nAssigned step: {node.objective}",
            "project_context": source_manifest["project_context"],
            "input_artifacts": inputs,
            "output_contract": node.output_contract,
            "authority": {
                "allowed_write_roots": list(node.allowed_write_roots),
                "external_side_effects": False,
                "may_publish": False,
            },
            "created_at": action["recorded_at"],
        }
        _validate_instance(profile_path, "task-envelope@2", task, f"task {task_id}")
        task_content = _canonical_json(task)
        task_ref = f"handoffs/{run_id}/{task_id}.json"
        writes.append(_write_item(task_ref, task_content))
        tasks.append(
            {
                "step_id": node.node_id,
                "task_id": task_id,
                "task_ref": task_ref,
                "task_sha256": _sha256(task_content),
                "status": "queued",
                "result_ref": None,
            }
        )
    if not tasks:
        raise ReworkError("source route has no materializable initial task")

    manifest = {
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "manifest_version": "2.0",
        "manifest_revision": 1,
        "previous_manifest": None,
        "run_id": run_id,
        "run_mode": source_manifest["run_mode"],
        "status": "queued",
        "objective": objective,
        "coordinator_plan": plan_pointer,
        "project_context": source_manifest["project_context"],
        "route_id": route.route_id,
        "route_contract": route_pointer,
        "planned_steps": [
            {
                "step_id": node.node_id,
                "role_id": node.role_id,
                "depends_on": list(node.depends_on),
                "output_contract": node.output_contract,
            }
            for node in route.nodes
        ],
        "tasks": tasks,
        "output_artifacts": [],
        "review_items": [],
        "publish_side_effects": False,
        "blocked_reason": None,
        "started_at": action["recorded_at"],
        "completed_at": None,
    }
    _validate_instance(profile_path, "run-manifest@2", manifest, "rework run manifest")
    manifest_content = _canonical_json(manifest)
    manifest_ref = f"records/runs/{run_id}/manifest-r0001.json"
    writes.append(_write_item(manifest_ref, manifest_content))

    receipt = _apply_request(
        workspace,
        profile_path,
        profile,
        run_id,
        writer_key,
        [{"path": next_action_ref, "expected_sha256": None}],
        writes,
    )
    if {item["path"] for item in receipt["writes"]} != {
        item["path"] for item in writes
    }:
        raise ReworkError("completed Root Writer receipt does not cover the rework run")
    return {
        "status": "prepared",
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "run_id": run_id,
        "source_run_id": source_run_id,
        "route_id": route.route_id,
        "expected_artifact_revision": expected_revision,
        "action": action_pointer,
        "lineage": lineage_pointer,
        "previous_artifact": previous_artifact,
        "manifest": _pointer(manifest_ref, "r0001", manifest_content),
        "initial_tasks": [
            {
                "step_id": task["step_id"],
                "task_id": task["task_id"],
                "task_ref": task["task_ref"],
                "task_sha256": task["task_sha256"],
            }
            for task in tasks
        ],
        "writer_receipt": receipt,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--project-config", required=True, type=Path)
    parser.add_argument("--action-ref", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = prepare_rework(
            args.workspace, args.project_config, args.action_ref
        )
    except ReworkError as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
