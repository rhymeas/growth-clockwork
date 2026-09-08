#!/usr/bin/env python3
"""Read-only resume/status view for one selected Growth Clockwork project.

The inspector resolves only the state directory pinned by ``project.json``.  It
does not repair, advance, dispatch, publish, call a model, invoke a shell, reach
the network, or create files.  A run is reported only after its complete
manifest chain, Root Writer receipts, task envelopes, and review pointer have
been verified against exact persisted bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Iterable
import uuid

from pipeline.agent_registry import (
    AgentRegistryError,
    ResolvedAgentRegistry,
    RouteContract,
    load_agent_registry,
    route_for_activation_decisions,
    route_with_active_nodes,
)
from pipeline import root_writer
from pipeline.validate_json_suites import (
    SuiteConfigurationError,
    ValidationError,
    validate_registered_instance,
)


MANIFEST_NAME_RE = re.compile(r"^manifest-r([0-9]{4,})\.json$")
RECEIPT_NAME_RE = re.compile(
    r"^writer-([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})\.json$"
)
RUN_STATUSES = (
    "queued",
    "running",
    "blocked",
    "awaiting_review",
    "completed",
    "failed",
)
TASK_STATUSES = (
    "queued",
    "running",
    "completed",
    "blocked",
    "needs_attention",
    "failed",
)
ROLE_SPEC_FIELDS = {
    "spec_version",
    "role_id",
    "display_name",
    "role_class",
    "mission",
    "capabilities",
    "owns",
    "does_not_own",
    "activation",
    "inputs",
    "outputs",
    "dependencies",
    "authority",
    "quality_rules",
    "stop_conditions",
    "handoff_contracts",
}
ROLE_AUTHORITY_FIELDS = {
    "autonomous_internal_work",
    "direct_persistence",
    "network",
    "external_side_effects",
    "may_publish",
    "may_send",
    "may_spend",
    "may_modify_accounts",
    "allowed_internal_actions",
}


class RuntimeStatusError(ValueError):
    """The selected runtime cannot be trusted enough to resume."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _pointer(ref: str, revision: str, content: bytes) -> dict[str, str]:
    return {
        "artifact_ref": ref,
        "artifact_revision": revision,
        "artifact_sha256": _sha256(content),
    }


def _json_object(content: bytes, label: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-JSON numeric constant {value}")

    try:
        value = json.loads(content.decode("utf-8"), parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeStatusError(f"cannot parse {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeStatusError(f"{label} must be a JSON object")
    return value


def _require_exact_keys(
    value: Any,
    required: set[str],
    optional: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeStatusError(f"{label} must be a JSON object")
    missing = sorted(required - value.keys())
    extra = sorted(value.keys() - required - optional)
    if missing:
        raise RuntimeStatusError(f"{label} missing fields: {', '.join(missing)}")
    if extra:
        raise RuntimeStatusError(f"{label} has unknown fields: {', '.join(extra)}")
    return value


def _format_errors(errors: Iterable[ValidationError]) -> str:
    return "; ".join(
        f"{error.instance_path} [{error.rule}]: {error.message}"
        for error in errors
    )


def _validate_instance(
    profile_path: Path,
    schema_id: str,
    value: dict[str, Any],
    label: str,
) -> None:
    try:
        errors = validate_registered_instance(profile_path, schema_id, value)
    except SuiteConfigurationError as exc:
        raise RuntimeStatusError(str(exc)) from exc
    if errors:
        raise RuntimeStatusError(
            f"{label} failed {schema_id}: {_format_errors(errors)}"
        )


def _open_state_readonly(workspace: Path, profile: dict[str, Any]) -> int | None:
    """Open the selected project state without using the writer's mkdir path."""

    try:
        workspace_fd = os.open(workspace, root_writer._directory_flags())
    except OSError as exc:
        raise RuntimeStatusError(f"cannot open workspace read-only: {exc}") from exc
    try:
        try:
            return root_writer._open_directory_chain(
                workspace_fd,
                profile["_state_root"].parts,
                create=False,
                label="selected project state",
            )
        except FileNotFoundError:
            return None
        except root_writer.WriterError as exc:
            raise RuntimeStatusError(str(exc)) from exc
    finally:
        os.close(workspace_fd)


def _open_directory(state_fd: int, relative: PurePosixPath, label: str) -> int | None:
    try:
        return root_writer._open_directory_chain(
            state_fd,
            relative.parts,
            create=False,
            label=label,
        )
    except FileNotFoundError:
        return None
    except root_writer.WriterError as exc:
        raise RuntimeStatusError(str(exc)) from exc


def _directory_entries(
    state_fd: int,
    relative: PurePosixPath,
    label: str,
) -> list[tuple[str, os.stat_result]] | None:
    directory_fd = _open_directory(state_fd, relative, label)
    if directory_fd is None:
        return None
    try:
        try:
            names = sorted(os.listdir(directory_fd))
        except OSError as exc:
            raise RuntimeStatusError(f"cannot list {label}: {exc}") from exc
        entries: list[tuple[str, os.stat_result]] = []
        for name in names:
            if name in {".", ".."} or "/" in name or "\x00" in name:
                raise RuntimeStatusError(f"unsafe entry in {label}: {name!r}")
            try:
                info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise RuntimeStatusError(
                    f"cannot inspect entry in {label}: {name}: {exc}"
                ) from exc
            entries.append((name, info))
        return entries
    finally:
        os.close(directory_fd)


def _state_bytes(state_fd: int, raw_ref: str, label: str) -> bytes:
    try:
        relative = root_writer._validate_relative_path(raw_ref, label)
        content = root_writer._read_relative_bytes(state_fd, relative)
    except root_writer.WriterError as exc:
        raise RuntimeStatusError(str(exc)) from exc
    assert content is not None
    return content


def _normal_pointer(pointer: Any, label: str) -> dict[str, str]:
    _require_exact_keys(
        pointer,
        {"artifact_ref", "artifact_revision", "artifact_sha256"},
        set(),
        label,
    )
    try:
        relative = root_writer._validate_relative_path(
            pointer["artifact_ref"], f"{label}.artifact_ref"
        )
    except root_writer.WriterError as exc:
        raise RuntimeStatusError(str(exc)) from exc
    revision = pointer["artifact_revision"]
    if not isinstance(revision, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", revision
    ):
        raise RuntimeStatusError(f"{label}.artifact_revision is invalid")
    digest = pointer["artifact_sha256"]
    if not isinstance(digest, str) or not root_writer.SHA256_RE.fullmatch(digest):
        raise RuntimeStatusError(f"{label}.artifact_sha256 is invalid")
    return {
        "artifact_ref": relative.as_posix(),
        "artifact_revision": revision,
        "artifact_sha256": digest,
    }


def _receipt_writes(
    receipt: dict[str, Any],
    profile: dict[str, Any],
    run_id: str,
    filename_key: str,
) -> list[dict[str, Any]]:
    _require_exact_keys(
        receipt,
        {
            "receipt_version",
            "status",
            "project_id",
            "project_profile_revision",
            "run_id",
            "idempotency_key",
            "requested_by",
            "request_sha256",
            "writes",
            "created_at",
        },
        set(),
        "writer receipt",
    )
    if (
        receipt["receipt_version"] != "1.0"
        or receipt["status"] != "completed"
        or receipt["project_id"] != profile["project_id"]
        or receipt["project_profile_revision"] != profile["profile_revision"]
        or receipt["run_id"] != run_id
        or receipt["requested_by"] != profile["root_role_id"]
    ):
        raise RuntimeStatusError("writer receipt identity or status is invalid")
    try:
        parsed_key = uuid.UUID(receipt["idempotency_key"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise RuntimeStatusError("writer receipt idempotency key is invalid") from exc
    if (
        str(parsed_key) != receipt["idempotency_key"]
        or receipt["idempotency_key"] != filename_key
    ):
        raise RuntimeStatusError("writer receipt idempotency key does not match its file")
    if not root_writer.SHA256_RE.fullmatch(str(receipt["request_sha256"])):
        raise RuntimeStatusError("writer receipt request hash is invalid")

    writes = receipt["writes"]
    if not isinstance(writes, list) or not writes:
        raise RuntimeStatusError("writer receipt has no writes")
    seen: set[str] = set()
    for item in writes:
        _require_exact_keys(
            item,
            {"path", "mode", "sha256", "bytes", "media_type"},
            set(),
            "writer receipt write",
        )
        try:
            relative = root_writer._validate_relative_path(
                item["path"], "writer receipt write path"
            )
        except root_writer.WriterError as exc:
            raise RuntimeStatusError(str(exc)) from exc
        if item["path"] in seen:
            raise RuntimeStatusError("writer receipt contains duplicate write paths")
        seen.add(item["path"])
        if not any(
            root_writer._under_prefix(relative, root)
            for root in profile["_allowed_roots"]
        ):
            raise RuntimeStatusError("writer receipt write is outside project roots")
        if root_writer._under_prefix(relative, profile["_receipt_root"]):
            raise RuntimeStatusError("writer receipt claims a write inside its receipt root")
        if item["mode"] != "create":
            raise RuntimeStatusError("writer receipt contains a non-create write")
        if not root_writer.SHA256_RE.fullmatch(str(item["sha256"])):
            raise RuntimeStatusError("writer receipt contains an invalid write hash")
        if (
            not isinstance(item["bytes"], int)
            or isinstance(item["bytes"], bool)
            or item["bytes"] < 0
        ):
            raise RuntimeStatusError("writer receipt contains an invalid byte count")
        if not isinstance(item["media_type"], str) or not item["media_type"].strip():
            raise RuntimeStatusError("writer receipt contains an invalid media type")
    return writes


def _receipt_index(
    state_fd: int,
    profile: dict[str, Any],
    run_id: str,
) -> dict[tuple[str, str, int], str]:
    relative = profile["_receipt_root"] / run_id
    entries = _directory_entries(state_fd, relative, f"writer receipts for {run_id}")
    if entries is None:
        raise RuntimeStatusError(
            f"completed Root Writer receipt directory is missing: {run_id}"
        )
    index: dict[tuple[str, str, int], str] = {}
    for name, info in entries:
        match = RECEIPT_NAME_RE.fullmatch(name)
        if match is None or not stat.S_ISREG(info.st_mode):
            raise RuntimeStatusError(
                f"unsafe file in Root Writer receipt directory: {name}"
            )
        ref = (relative / name).as_posix()
        receipt = _json_object(_state_bytes(state_fd, ref, "writer receipt"), ref)
        for item in _receipt_writes(receipt, profile, run_id, match.group(1)):
            key = (item["path"], item["sha256"], item["bytes"])
            existing = index.get(key)
            if existing is not None and existing != ref:
                raise RuntimeStatusError(
                    f"multiple receipts claim the same exact write: {item['path']}"
                )
            index[key] = ref
    if not index:
        raise RuntimeStatusError(f"no completed Root Writer receipt exists for {run_id}")
    return index


def _require_receipt(
    receipts: dict[tuple[str, str, int], str],
    artifact_ref: str,
    content: bytes,
) -> str:
    receipt_ref = receipts.get((artifact_ref, _sha256(content), len(content)))
    if receipt_ref is None:
        raise RuntimeStatusError(
            f"no completed Root Writer receipt covers exact bytes for {artifact_ref}"
        )
    return receipt_ref


def _pinned_bytes(
    state_fd: int,
    receipts: dict[tuple[str, str, int], str],
    pointer: Any,
    label: str,
) -> bytes:
    normalized = _normal_pointer(pointer, label)
    content = _state_bytes(state_fd, normalized["artifact_ref"], label)
    if _sha256(content) != normalized["artifact_sha256"]:
        raise RuntimeStatusError(f"{label} hash does not match persisted bytes")
    _require_receipt(receipts, normalized["artifact_ref"], content)
    return content


def _latest_manifest(
    state_fd: int,
    profile_path: Path,
    profile: dict[str, Any],
    run_id: str,
    receipts: dict[tuple[str, str, int], str],
) -> tuple[dict[str, Any], str, bytes]:
    relative = PurePosixPath("records") / "runs" / run_id
    entries = _directory_entries(state_fd, relative, f"run manifests for {run_id}")
    if entries is None:
        raise RuntimeStatusError(f"run manifest directory is missing: {run_id}")
    versions: dict[int, tuple[str, bytes, dict[str, Any]]] = {}
    for name, info in entries:
        match = MANIFEST_NAME_RE.fullmatch(name)
        if match is None or not stat.S_ISREG(info.st_mode):
            raise RuntimeStatusError(f"unsafe file in run manifest directory: {name}")
        revision = int(match.group(1))
        if revision in versions:
            raise RuntimeStatusError(f"duplicate run manifest revision: {revision}")
        ref = (relative / name).as_posix()
        content = _state_bytes(state_fd, ref, "run manifest")
        manifest = _json_object(content, ref)
        _validate_instance(profile_path, "run-manifest@2", manifest, ref)
        if (
            manifest["project_id"] != profile["project_id"]
            or manifest["project_profile_revision"] != profile["profile_revision"]
            or manifest["run_id"] != run_id
            or manifest["manifest_revision"] != revision
        ):
            raise RuntimeStatusError(f"run manifest identity mismatch: {ref}")
        versions[revision] = (ref, content, manifest)
    if not versions:
        raise RuntimeStatusError(f"run has no manifest: {run_id}")
    expected = list(range(1, max(versions) + 1))
    if sorted(versions) != expected:
        raise RuntimeStatusError("run manifest revision chain has a gap")
    for revision in expected:
        ref, content, manifest = versions[revision]
        if revision == 1:
            if manifest["previous_manifest"] is not None:
                raise RuntimeStatusError("run manifest r0001 has a predecessor")
        else:
            previous_ref, previous_content, _ = versions[revision - 1]
            previous = _pointer(
                previous_ref, f"r{revision - 1:04d}", previous_content
            )
            if manifest["previous_manifest"] != previous:
                raise RuntimeStatusError(
                    f"run manifest revision {revision} has a broken predecessor"
                )
        _require_receipt(receipts, ref, content)
    return versions[max(versions)][2], versions[max(versions)][0], versions[max(versions)][1]


def _verify_route(
    state_fd: int,
    receipts: dict[tuple[str, str, int], str],
    profile: dict[str, Any],
    registry: ResolvedAgentRegistry,
    manifest: dict[str, Any],
    plan: dict[str, Any],
) -> RouteContract:
    base_route = registry.routes.get(manifest["route_id"])
    if base_route is None or manifest["route_id"] not in set(profile.get("enabled_routes", [])):
        raise RuntimeStatusError("run route is not enabled by the selected profile")
    snapshot = _pinned_bytes(
        state_fd, receipts, manifest["route_contract"], "manifest route_contract"
    )
    if snapshot != base_route.artifact.content:
        raise RuntimeStatusError("run route snapshot differs from the profile-pinned route")
    raw_steps = manifest["planned_steps"]
    active_ids = {
        item.get("step_id")
        for item in raw_steps
        if isinstance(item, dict) and isinstance(item.get("step_id"), str)
    }
    try:
        route = route_with_active_nodes(base_route, active_ids)
        if "step_activation" in plan["route_decision"]:
            expected_route = route_for_activation_decisions(
                base_route,
                plan["route_decision"],
                require_step_activation=True,
            )
            if [node.node_id for node in expected_route.nodes] != [
                node.node_id for node in route.nodes
            ]:
                raise RuntimeStatusError(
                    "coordinator plan step activation differs from the run manifest"
                )
    except AgentRegistryError as exc:
        raise RuntimeStatusError(str(exc)) from exc
    expected_steps = [
        {
            "step_id": node.node_id,
            "role_id": node.role_id,
            "depends_on": list(node.depends_on),
            "output_contract": node.output_contract,
        }
        for node in route.nodes
    ]
    if manifest["planned_steps"] != expected_steps:
        raise RuntimeStatusError("run planned steps differ from the pinned route")
    return route


def _verify_run_inputs(
    workspace: Path,
    state_fd: int,
    profile_path: Path,
    receipts: dict[tuple[str, str, int], str],
    profile: dict[str, Any],
    registry: ResolvedAgentRegistry,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    plan_content = _pinned_bytes(
        state_fd, receipts, manifest["coordinator_plan"], "coordinator plan"
    )
    plan = _json_object(plan_content, "coordinator plan")
    _validate_instance(profile_path, "coordinator-plan@1", plan, "coordinator plan")
    if (
        plan["project_id"] != profile["project_id"]
        or plan["project_profile_revision"] != profile["profile_revision"]
        or plan["run_mode"] != manifest["run_mode"]
        or plan["route_decision"]["selected_route_id"] != manifest["route_id"]
        or plan["objective"] != manifest["objective"]
    ):
        raise RuntimeStatusError("coordinator plan does not match the run manifest")
    context_ref = manifest["project_context"]["artifact_ref"]
    if context_ref.startswith(f"records/run-inputs/{manifest['run_id']}/context/"):
        _pinned_bytes(state_fd, receipts, manifest["project_context"], "project context")
    else:
        # A note-driven run retains the original context. Verify that exact note
        # and complete source lineage instead of accepting arbitrary cross-run pins.
        from pipeline import rework

        notes = [pointer for pointer in plan["diagnosis"]["evidence_refs"]
                 if pointer["artifact_ref"].startswith("outbox/review-actions/")]
        if len(notes) != 1:
            raise RuntimeStatusError("shared context requires one exact rework note")
        try:
            action, action_pointer, _ = rework._action_and_pointer(
                workspace, profile_path, profile, notes[0]["artifact_ref"]
            )
            lineage, lineage_pointer, _ = rework._lineage(workspace, profile, action)
            source, _, _, _, _ = rework._source_run(
                workspace, profile_path, profile, registry, action, lineage
            )
        except (rework.ReworkError, ValueError, OSError) as exc:
            raise RuntimeStatusError(f"rework source verification failed: {exc}") from exc
        if (
            action_pointer != notes[0]
            or lineage_pointer not in plan["diagnosis"]["evidence_refs"]
            or source["project_context"] != manifest["project_context"]
            or source["route_id"] != manifest["route_id"]
        ):
            raise RuntimeStatusError("rework note source does not match the pinned context or route")
    return plan


def _verify_task_role_snapshot(
    content: bytes,
    pointer: Any,
    manifest: dict[str, Any],
    expected_role: str,
    expected_deliverable_contract: str,
) -> None:
    """Validate one immutable run role without trusting today's role bytes."""

    normalized = _normal_pointer(pointer, "task role_contract")
    expected_ref = (
        f"records/run-inputs/{manifest['run_id']}/agents/roles/{expected_role}.json"
    )
    if (
        normalized["artifact_ref"] != expected_ref
        or normalized["artifact_revision"] != expected_role
    ):
        raise RuntimeStatusError(
            f"task role snapshot identity is not canonical: {expected_role}"
        )

    snapshot = _json_object(content, f"task role snapshot {expected_role}")
    _require_exact_keys(
        snapshot,
        ROLE_SPEC_FIELDS,
        set(),
        f"task role snapshot {expected_role}",
    )
    if (
        snapshot["spec_version"] != "1.0"
        or snapshot["role_id"] != expected_role
    ):
        raise RuntimeStatusError(
            f"task role snapshot identity/version mismatch: {expected_role}"
        )

    authority = _require_exact_keys(
        snapshot["authority"],
        ROLE_AUTHORITY_FIELDS,
        set(),
        f"task role snapshot {expected_role} authority",
    )
    if (
        authority["autonomous_internal_work"] is not True
        or authority["network"]
        not in {"none", "allowlisted-read-only", "adapter-read-only"}
        or any(
            authority[field] is not False
            for field in (
                "direct_persistence",
                "external_side_effects",
                "may_publish",
                "may_send",
                "may_spend",
                "may_modify_accounts",
            )
        )
        or not isinstance(authority["allowed_internal_actions"], list)
        or not authority["allowed_internal_actions"]
        or not all(
            isinstance(item, str) and item.strip()
            for item in authority["allowed_internal_actions"]
        )
    ):
        raise RuntimeStatusError(
            f"task role snapshot authority is invalid: {expected_role}"
        )

    if snapshot["handoff_contracts"] != {
        "task": "task-envelope@2",
        "result": "result-envelope@2",
    }:
        raise RuntimeStatusError(
            f"task role snapshot handoff contracts are stale: {expected_role}"
        )
    outputs = snapshot["outputs"]
    if not isinstance(outputs, list) or not any(
        isinstance(output, dict)
        and output.get("contract") == expected_deliverable_contract
        for output in outputs
    ):
        raise RuntimeStatusError(
            "task role snapshot does not own its route deliverable: "
            f"{expected_role}/{expected_deliverable_contract}"
        )


def _verify_task(
    state_fd: int,
    profile_path: Path,
    receipts: dict[tuple[str, str, int], str],
    profile: dict[str, Any],
    registry: ResolvedAgentRegistry,
    manifest: dict[str, Any],
    node: Any,
    task_entry: dict[str, Any],
) -> tuple[dict[str, Any], bytes]:
    expected_ref = f"handoffs/{manifest['run_id']}/{task_entry['task_id']}.json"
    if task_entry["task_ref"] != expected_ref:
        raise RuntimeStatusError("task manifest reference is not canonical for this run")
    content = _state_bytes(state_fd, expected_ref, "task envelope")
    if _sha256(content) != task_entry["task_sha256"]:
        raise RuntimeStatusError("task bytes do not match the manifest task hash")
    _require_receipt(receipts, expected_ref, content)
    task = _json_object(content, f"task {task_entry['task_id']}")
    _validate_instance(profile_path, "task-envelope@2", task, "task envelope")
    if (
        task["project_id"] != profile["project_id"]
        or task["project_profile_revision"] != profile["profile_revision"]
        or task["run_id"] != manifest["run_id"]
        or task["task_id"] != task_entry["task_id"]
        or task["step_id"] != task_entry["step_id"]
        or task["route_id"] != manifest["route_id"]
        or task["route_contract"] != manifest["route_contract"]
        or task["project_context"] != manifest["project_context"]
        or task["assigned_role"] != node.role_id
        or task["output_contract"] != node.output_contract
    ):
        raise RuntimeStatusError("task identity or pinned route does not match the manifest")
    role = _pinned_bytes(
        state_fd, receipts, task["role_contract"], "task role_contract"
    )
    expected_role = node.role_id
    _verify_task_role_snapshot(
        role,
        task["role_contract"],
        manifest,
        expected_role,
        node.deliverable_contract,
    )
    role_contract = registry.roles.get(expected_role)
    if role_contract is None:
        raise RuntimeStatusError(f"unknown task role in selected registry: {expected_role}")
    if (
        task_entry["status"] != "completed"
        and role != role_contract.artifact.content
    ):
        raise RuntimeStatusError(f"task role snapshot differs from pinned role: {expected_role}")
    return task, content


def _verify_result(
    state_fd: int,
    profile_path: Path,
    receipts: dict[tuple[str, str, int], str],
    profile: dict[str, Any],
    manifest: dict[str, Any],
    task_entry: dict[str, Any],
    task: dict[str, Any],
) -> None:
    pointer = task_entry["result_ref"]
    if task_entry["status"] in {"queued", "running"}:
        if pointer is not None:
            raise RuntimeStatusError("non-terminal task unexpectedly has a result")
        return
    content = _pinned_bytes(state_fd, receipts, pointer, "task result_ref")
    result = _json_object(content, "persisted result envelope")
    _validate_instance(profile_path, "result-envelope@2", result, "persisted result")
    if (
        result["project_id"] != profile["project_id"]
        or result["project_profile_revision"] != profile["profile_revision"]
        or result["run_id"] != manifest["run_id"]
        or result["task_id"] != task_entry["task_id"]
        or result["task_sha256"] != task_entry["task_sha256"]
        or result["step_id"] != task_entry["step_id"]
        or result["role_id"] != task["assigned_role"]
        or result["status"] != task_entry["status"]
    ):
        raise RuntimeStatusError("persisted result identity or status does not match its task")


def _review_pointer(
    state_fd: int,
    profile_path: Path,
    receipts: dict[tuple[str, str, int], str],
    profile: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, str] | None:
    if not manifest["review_items"]:
        return None
    pointer = _normal_pointer(manifest["review_items"][-1], "review pointer")
    content = _pinned_bytes(state_fd, receipts, pointer, "review pointer")
    review = _json_object(content, "outbox review item")
    _validate_instance(
        profile_path, "outbox-review-item@1", review, "outbox review item"
    )
    if (
        review["project_id"] != profile["project_id"]
        or review["project_profile_revision"] != profile["profile_revision"]
        or review["artifact_revision"] != pointer["artifact_revision"]
    ):
        raise RuntimeStatusError("outbox review item belongs to another project or revision")
    artifact = _state_bytes(state_fd, review["artifact_path"], "review artifact")
    if _sha256(artifact) != review["artifact_sha256"]:
        raise RuntimeStatusError("review artifact bytes do not match the review item hash")
    _require_receipt(receipts, review["artifact_path"], artifact)
    return pointer


def _run_status(
    workspace: Path,
    state_fd: int,
    profile_path: Path,
    profile: dict[str, Any],
    registry: ResolvedAgentRegistry,
    run_id: str,
) -> dict[str, Any]:
    receipts = _receipt_index(state_fd, profile, run_id)
    manifest, manifest_ref, manifest_content = _latest_manifest(
        state_fd, profile_path, profile, run_id, receipts
    )
    plan = _verify_run_inputs(workspace, state_fd, profile_path, receipts, profile, registry, manifest)
    route = _verify_route(state_fd, receipts, profile, registry, manifest, plan)
    nodes = {node.node_id: node for node in route.nodes}

    by_step: dict[str, dict[str, Any]] = {}
    verified: dict[str, dict[str, Any]] = {}
    task_counts = {status: 0 for status in TASK_STATUSES}
    for task_entry in manifest["tasks"]:
        step_id = task_entry["step_id"]
        if step_id in by_step or step_id not in nodes:
            raise RuntimeStatusError("run manifest has a duplicate or unknown task step")
        by_step[step_id] = task_entry
        task, _ = _verify_task(
            state_fd,
            profile_path,
            receipts,
            profile,
            registry,
            manifest,
            nodes[step_id],
            task_entry,
        )
        _verify_result(
            state_fd,
            profile_path,
            receipts,
            profile,
            manifest,
            task_entry,
            task,
        )
        verified[step_id] = task
        task_counts[task_entry["status"]] += 1

    if "step_activation" in plan["route_decision"] and (
        task_counts["queued"] + task_counts["running"]
        > route.max_concurrent_workhorses
    ):
        raise RuntimeStatusError("run exceeds the route workhorse concurrency cap")

    for step_id, task in verified.items():
        node = nodes[step_id]
        if any(dependency not in by_step for dependency in node.depends_on):
            raise RuntimeStatusError("materialized task is missing a route dependency")
        expected_dependency_ids = [
            by_step[dependency]["task_id"] for dependency in node.depends_on
        ]
        if task["depends_on"] != expected_dependency_ids:
            raise RuntimeStatusError("task dependency IDs differ from the route")

    dispatchable: list[dict[str, str]] = []
    for step_id, task_entry in by_step.items():
        if task_entry["status"] != "queued":
            continue
        node = nodes[step_id]
        dependencies = [by_step.get(dependency) for dependency in node.depends_on]
        if any(item is None or item["status"] != "completed" for item in dependencies):
            continue
        task = verified[step_id]
        dispatchable.append(
            {
                "step": step_id,
                "role": task["assigned_role"],
                "task_ref": task_entry["task_ref"],
                "task_sha256": task_entry["task_sha256"],
            }
        )

    review = _review_pointer(
        state_fd, profile_path, receipts, profile, manifest
    )
    if manifest["status"] == "awaiting_review" and review is None:
        raise RuntimeStatusError("awaiting_review run has no verified review pointer")
    return {
        "run_id": run_id,
        "route_id": manifest["route_id"],
        "run_status": manifest["status"],
        "manifest": _pointer(
            manifest_ref,
            f"r{manifest['manifest_revision']:04d}",
            manifest_content,
        ),
        "task_counts": task_counts,
        "dispatchable_tasks": dispatchable,
        "review_pointer": review,
        "blocked_reason": manifest["blocked_reason"],
    }


def inspect_runtime(workspace: Path, profile_path: Path) -> dict[str, Any]:
    """Return a verified, read-only resume view for one selected project profile."""

    try:
        workspace = root_writer._validate_workspace(workspace)
        profile = root_writer.load_project_profile(profile_path)
        root_writer._validate_profile_package(workspace, profile_path, profile)
    except root_writer.WriterError as exc:
        raise RuntimeStatusError(str(exc)) from exc
    if "_agent_registry" not in profile:
        raise RuntimeStatusError("project profile has no pinned agent registry")
    try:
        registry = load_agent_registry(workspace, profile_path, profile["_agent_registry"])
    except AgentRegistryError as exc:
        raise RuntimeStatusError(str(exc)) from exc

    state_fd = _open_state_readonly(workspace, profile)
    status_counts = {status: 0 for status in RUN_STATUSES}
    if state_fd is None:
        return {
            "status": "empty",
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
            "status_counts": status_counts,
            "runs": [],
        }
    try:
        run_entries = _directory_entries(
            state_fd, PurePosixPath("records/runs"), "project run directory"
        )
        if run_entries is None:
            runs: list[dict[str, Any]] = []
        else:
            runs = []
            for run_id, info in run_entries:
                if not stat.S_ISDIR(info.st_mode) or not root_writer.RUN_ID_RE.fullmatch(run_id):
                    raise RuntimeStatusError(
                        f"unsafe entry in project run directory: {run_id}"
                    )
                run = _run_status(
                    workspace,
                    state_fd,
                    profile_path,
                    profile,
                    registry,
                    run_id,
                )
                status_counts[run["run_status"]] += 1
                runs.append(run)
    finally:
        os.close(state_fd)
    return {
        "status": "ready" if runs else "empty",
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "status_counts": status_counts,
        "runs": runs,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--project-config", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = inspect_runtime(args.workspace, args.project_config)
    except RuntimeStatusError as exc:
        print(
            json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
