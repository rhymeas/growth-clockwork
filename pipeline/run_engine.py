#!/usr/bin/env python3
"""Accept immutable role results and advance one project-neutral route DAG.

The engine only verifies, materializes, and persists. It never calls a model,
network, shell, scheduler, publisher, or external account. Every accepted change
is one Root Writer transaction and every final product remains review-only.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import fcntl
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
from typing import Any, Iterable, Iterator
import uuid

from pipeline.agent_registry import (
    AgentRegistryError,
    RegistryArtifact,
    RoleContract,
    RouteContract,
    RouteNode,
    role_contract_from_artifact,
    route_contract_from_artifact,
    route_for_activation_decisions,
    route_with_active_nodes,
)
from pipeline import root_writer
from pipeline.professional_contracts import (
    ProfessionalContractError,
    ProfessionalContractBundle,
    load_professional_contracts,
    validate_deliverable_bytes,
)
from pipeline.operating_contract import (
    OperatingContractError,
    enforce_run_limits,
)
from pipeline.validate_json_suites import (
    SuiteConfigurationError,
    ValidationError,
    validate_registered_instance,
)


WRITER_NAMESPACE = uuid.UUID("0cc4342a-87de-4a93-a2ff-e99b8b71f5e4")
MANIFEST_NAME_RE = re.compile(r"^manifest-r([0-9]{4,})\.json$")
RECEIPT_NAME_RE = re.compile(
    r"^writer-([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\.json$"
)
TERMINAL_TASK_STATUSES = {"completed", "blocked", "needs_attention", "failed"}
SUBMISSION_REQUIRED = {
    "submission_version",
    "project_id",
    "project_profile_revision",
    "run_id",
    "task_id",
    "result",
    "artifacts",
}
ARTIFACT_REQUIRED = {
    "artifact_ref",
    "artifact_revision",
    "artifact_sha256",
    "media_type",
    "content",
}
REVIEW_REQUIRED = {
    "artifact_ref",
    "artifact_revision",
    "artifact_sha256",
    "artifact_id",
    "title",
    "type",
    "preview",
    "evidence",
    "quality_checks",
}


class RunEngineError(ValueError):
    """Raised before a submitted result may advance or terminate a run."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _strict_json_object(path: Path, label: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-JSON numeric constant {value}")

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), parse_constant=reject_constant
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RunEngineError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise RunEngineError(f"{label} must be a JSON object")
    return value


def _json_object_bytes(content: bytes, label: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-JSON numeric constant {value}")

    try:
        value = json.loads(content.decode("utf-8"), parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RunEngineError(f"cannot parse {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise RunEngineError(f"{label} must be a JSON object")
    return value


def _require_exact_keys(
    value: Any,
    required: set[str],
    optional: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RunEngineError(f"{label} must be a JSON object")
    missing = sorted(required - value.keys())
    extra = sorted(value.keys() - required - optional)
    if missing:
        raise RunEngineError(f"{label} missing fields: {', '.join(missing)}")
    if extra:
        raise RunEngineError(f"{label} has unknown fields: {', '.join(extra)}")
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
        raise RunEngineError(str(exc)) from exc
    if errors:
        raise RunEngineError(
            f"{label} failed {schema_id}: {_format_errors(errors)}"
        )


def _pointer(ref: str, revision: str, content: bytes) -> dict[str, str]:
    return {
        "artifact_ref": ref,
        "artifact_revision": revision,
        "artifact_sha256": _sha256(content),
    }


def _write_item(
    path: str,
    content: bytes,
    media_type: str = "application/json",
) -> dict[str, Any]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RunEngineError(f"Root Writer input is not UTF-8: {path}") from exc
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
        raise RunEngineError(str(exc)) from exc


def _pinned_bytes(
    workspace: Path,
    profile: dict[str, Any],
    pointer: dict[str, Any],
    label: str,
) -> bytes:
    try:
        normalized = root_writer._validate_pinned_reference(pointer, label)
    except root_writer.WriterError as exc:
        raise RunEngineError(str(exc)) from exc
    content = _state_bytes(workspace, profile, normalized["artifact_ref"])
    assert content is not None
    if _sha256(content) != normalized["artifact_sha256"]:
        raise RunEngineError(f"{label} hash does not match persisted bytes")
    return content


@contextlib.contextmanager
def _run_lock(workspace: Path, run_id: str) -> Iterator[None]:
    fingerprint = _sha256(f"{workspace}\n{run_id}".encode("utf-8"))[:24]
    lock_path = Path(tempfile.gettempdir()) / f"growth-run-engine-{fingerprint}.lock"
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _receipt_writes(
    receipt: dict[str, Any],
    profile: dict[str, Any],
    run_id: str,
) -> list[dict[str, Any]]:
    required = {
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
    }
    _require_exact_keys(receipt, required, set(), "writer receipt")
    if (
        receipt["receipt_version"] != "1.0"
        or receipt["status"] != "completed"
        or receipt["project_id"] != profile["project_id"]
        or receipt["project_profile_revision"] != profile["profile_revision"]
        or receipt["run_id"] != run_id
        or receipt["requested_by"] != profile["root_role_id"]
    ):
        raise RunEngineError("writer receipt identity or status is invalid")
    try:
        parsed = uuid.UUID(receipt["idempotency_key"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise RunEngineError("writer receipt idempotency key is invalid") from exc
    if str(parsed) != receipt["idempotency_key"]:
        raise RunEngineError("writer receipt idempotency key is not canonical")
    if not root_writer.SHA256_RE.fullmatch(str(receipt["request_sha256"])):
        raise RunEngineError("writer receipt request hash is invalid")
    writes = receipt["writes"]
    if not isinstance(writes, list) or not writes:
        raise RunEngineError("writer receipt has no writes")
    for item in writes:
        _require_exact_keys(
            item,
            {"path", "mode", "sha256", "bytes", "media_type"},
            set(),
            "writer receipt write",
        )
        try:
            root_writer._validate_relative_path(item["path"], "receipt write path")
        except root_writer.WriterError as exc:
            raise RunEngineError(str(exc)) from exc
        if item["mode"] != "create":
            raise RunEngineError("writer receipt contains a non-create write")
        if not root_writer.SHA256_RE.fullmatch(str(item["sha256"])):
            raise RunEngineError("writer receipt contains an invalid write hash")
        if (
            not isinstance(item["bytes"], int)
            or isinstance(item["bytes"], bool)
            or item["bytes"] < 0
        ):
            raise RunEngineError("writer receipt contains an invalid byte count")
        if not isinstance(item["media_type"], str) or not item["media_type"].strip():
            raise RunEngineError("writer receipt contains an invalid media type")
    return writes


def _receipts_for_run(
    workspace: Path,
    profile: dict[str, Any],
    run_id: str,
) -> list[tuple[str, dict[str, Any], list[dict[str, Any]]]]:
    receipt_root = profile["_receipt_root"].as_posix()
    relative_dir = PurePosixPath(receipt_root) / run_id
    directory = workspace / profile["_state_root"] / relative_dir
    if directory.is_symlink() or not directory.is_dir():
        raise RunEngineError(f"completed Root Writer receipt directory is missing: {run_id}")
    receipts: list[tuple[str, dict[str, Any], list[dict[str, Any]]]] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        match = RECEIPT_NAME_RE.fullmatch(path.name)
        if path.is_symlink() or not path.is_file() or match is None:
            raise RunEngineError(f"unsafe file in Root Writer receipt directory: {path.name}")
        ref = (relative_dir / path.name).as_posix()
        content = _state_bytes(workspace, profile, ref)
        assert content is not None
        receipt = _json_object_bytes(content, f"writer receipt {ref}")
        if receipt.get("idempotency_key") != match.group(1):
            raise RunEngineError(
                "writer receipt idempotency key does not match its file"
            )
        receipts.append((ref, receipt, _receipt_writes(receipt, profile, run_id)))
    if not receipts:
        raise RunEngineError(f"no completed Root Writer receipt exists for {run_id}")
    return receipts


def _receipt_covering(
    workspace: Path,
    profile: dict[str, Any],
    run_id: str,
    artifact_ref: str,
    content: bytes,
    *,
    media_type: str | None = None,
) -> tuple[str, dict[str, Any]]:
    digest = _sha256(content)
    match = _matching_receipt(
        _receipts_for_run(workspace, profile, run_id),
        artifact_ref,
        digest,
        len(content),
        media_type,
    )
    if match is not None:
        return match
    raise RunEngineError(
        f"no completed Root Writer receipt covers exact bytes for {artifact_ref}"
    )


def _matching_receipt(
    receipts: list[tuple[str, dict[str, Any], list[dict[str, Any]]]],
    artifact_ref: str,
    digest: str,
    byte_count: int,
    media_type: str | None,
) -> tuple[str, dict[str, Any]] | None:
    for receipt_ref, receipt, writes in receipts:
        for item in writes:
            if (
                item["path"] == artifact_ref
                and item["sha256"] == digest
                and item["bytes"] == byte_count
                and (media_type is None or item["media_type"] == media_type)
            ):
                return receipt_ref, receipt
    return None


def _receipt_covering_project_state(
    workspace: Path,
    profile: dict[str, Any],
    preferred_run_id: str,
    artifact_ref: str,
    content: bytes,
) -> tuple[str, dict[str, Any]]:
    digest = _sha256(content)
    match = _matching_receipt(
        _receipts_for_run(workspace, profile, preferred_run_id),
        artifact_ref,
        digest,
        len(content),
        None,
    )
    if match is not None:
        return match

    receipt_root = workspace / profile["_state_root"] / profile["_receipt_root"]
    if receipt_root.is_symlink() or not receipt_root.is_dir():
        raise RunEngineError("project Root Writer receipt root is missing or unsafe")
    for run_directory in sorted(receipt_root.iterdir(), key=lambda item: item.name):
        if run_directory.name == preferred_run_id:
            continue
        if (
            run_directory.is_symlink()
            or not run_directory.is_dir()
            or not root_writer.RUN_ID_RE.fullmatch(run_directory.name)
        ):
            raise RunEngineError(
                f"unsafe entry in project Root Writer receipt root: {run_directory.name}"
            )
        match = _matching_receipt(
            _receipts_for_run(workspace, profile, run_directory.name),
            artifact_ref,
            digest,
            len(content),
            None,
        )
        if match is not None:
            return match
    raise RunEngineError(
        f"no completed Root Writer receipt covers exact bytes for {artifact_ref}"
    )


def _latest_manifest(
    workspace: Path,
    profile_path: Path,
    profile: dict[str, Any],
    run_id: str,
) -> tuple[dict[str, Any], str, bytes]:
    relative_dir = PurePosixPath("records") / "runs" / run_id
    directory = workspace / profile["_state_root"] / relative_dir
    if directory.is_symlink() or not directory.is_dir():
        raise RunEngineError(f"run manifest directory is missing: {run_id}")
    versions: dict[int, tuple[str, bytes, dict[str, Any]]] = {}
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        match = MANIFEST_NAME_RE.fullmatch(path.name)
        if path.is_symlink() or not path.is_file() or match is None:
            raise RunEngineError(f"unsafe file in run manifest directory: {path.name}")
        revision = int(match.group(1))
        if revision in versions:
            raise RunEngineError(f"duplicate run manifest revision: {revision}")
        ref = (relative_dir / path.name).as_posix()
        content = _state_bytes(workspace, profile, ref)
        assert content is not None
        manifest = _json_object_bytes(content, f"run manifest {ref}")
        _validate_instance(profile_path, "run-manifest@2", manifest, ref)
        if (
            manifest["project_id"] != profile["project_id"]
            or manifest["project_profile_revision"] != profile["profile_revision"]
            or manifest["run_id"] != run_id
            or manifest["manifest_revision"] != revision
        ):
            raise RunEngineError(f"run manifest identity mismatch: {ref}")
        versions[revision] = (ref, content, manifest)
    if not versions:
        raise RunEngineError(f"run has no manifest: {run_id}")
    expected = list(range(1, max(versions) + 1))
    if sorted(versions) != expected:
        raise RunEngineError("run manifest revision chain has a gap")
    for revision in expected:
        ref, content, manifest = versions[revision]
        if revision == 1:
            if manifest["previous_manifest"] is not None:
                raise RunEngineError("run manifest r0001 has a predecessor")
        else:
            previous_ref, previous_content, _ = versions[revision - 1]
            expected_pointer = _pointer(
                previous_ref, f"r{revision - 1:04d}", previous_content
            )
            if manifest["previous_manifest"] != expected_pointer:
                raise RunEngineError(
                    f"run manifest revision {revision} has a broken predecessor"
                )
        _receipt_covering(workspace, profile, run_id, ref, content)
    return versions[max(versions)][2], versions[max(versions)][0], versions[max(versions)][1]


def _load_profile(
    workspace: Path, profile_path: Path
) -> tuple[Path, dict[str, Any]]:
    try:
        workspace = root_writer._validate_workspace(workspace)
        profile = root_writer.load_project_profile(profile_path)
        root_writer._validate_profile_package(workspace, profile_path, profile)
    except root_writer.WriterError as exc:
        raise RunEngineError(str(exc)) from exc
    return workspace, profile


def _submission(path: Path) -> dict[str, Any]:
    submission = _strict_json_object(path, "result submission")
    _require_exact_keys(
        submission, SUBMISSION_REQUIRED, {"review_candidate"}, "result submission"
    )
    if submission["submission_version"] != "1.0":
        raise RunEngineError("unsupported result submission version")
    if not isinstance(submission["result"], dict):
        raise RunEngineError("result submission result must be an object")
    artifacts = submission["artifacts"]
    if not isinstance(artifacts, list):
        raise RunEngineError("result submission artifacts must be an array")
    for index, artifact in enumerate(artifacts):
        _require_exact_keys(
            artifact, ARTIFACT_REQUIRED, set(), f"result submission artifacts[{index}]"
        )
    if "review_candidate" in submission:
        _require_exact_keys(
            submission["review_candidate"],
            REVIEW_REQUIRED,
            set(),
            "result submission review_candidate",
        )
    return submission


def _frozen_registry_entries(
    workspace: Path,
    profile: dict[str, Any],
    manifest: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    run_id = manifest["run_id"]
    registry_ref = f"records/run-inputs/{run_id}/agents/registry.json"
    content = _state_bytes(workspace, profile, registry_ref)
    assert content is not None
    _receipt_covering(workspace, profile, run_id, registry_ref, content)
    registry = _json_object_bytes(content, "frozen agent registry")
    _require_exact_keys(
        registry,
        {
            "registry_version",
            "registry_revision",
            "runtime",
            "schemas",
            "activation_modes",
            "roles",
            "routes",
            "invariants",
        },
        set(),
        "frozen agent registry",
    )
    if registry["registry_version"] != "1.0" or not root_writer.RUN_ID_RE.fullmatch(
        str(registry["registry_revision"])
    ):
        raise RunEngineError("frozen agent registry identity/version is invalid")
    invariants = _require_exact_keys(
        registry["invariants"],
        {
            "one_project_per_run",
            "project_context_required",
            "roles_are_read_only",
            "root_only_persistence",
            "governance_before_review",
            "human_review_exact_revision",
            "approval_is_not_publication",
            "publisher_present",
        },
        set(),
        "frozen agent registry invariants",
    )
    if any(
        invariants[field] is not True
        for field in (
            "one_project_per_run",
            "project_context_required",
            "roles_are_read_only",
            "root_only_persistence",
            "governance_before_review",
            "human_review_exact_revision",
            "approval_is_not_publication",
        )
    ) or invariants["publisher_present"] is not False:
        raise RunEngineError("frozen agent registry invariants are invalid")

    role_entries: dict[str, dict[str, Any]] = {}
    raw_roles = registry["roles"]
    if not isinstance(raw_roles, list) or not raw_roles:
        raise RunEngineError("frozen agent registry has no roles")
    for raw_entry in raw_roles:
        entry = _require_exact_keys(
            raw_entry,
            {"role_id", "role_class", "spec_ref", "sha256"},
            set(),
            "frozen role registry entry",
        )
        role_id = entry["role_id"]
        if (
            not isinstance(role_id, str)
            or not root_writer.RUN_ID_RE.fullmatch(role_id)
            or role_id in role_entries
            or entry["role_class"] not in {"core", "specialist"}
            or entry["spec_ref"]
            != f"roles/{entry['role_class'] if entry['role_class'] == 'core' else 'specialists'}/{role_id}.json"
            or not root_writer.SHA256_RE.fullmatch(str(entry["sha256"]))
        ):
            raise RunEngineError(f"frozen role registry entry is invalid: {role_id}")
        role_entries[role_id] = entry

    route_entries: dict[str, dict[str, Any]] = {}
    raw_routes = registry["routes"]
    if not isinstance(raw_routes, list) or not raw_routes:
        raise RunEngineError("frozen agent registry has no routes")
    for raw_entry in raw_routes:
        entry = _require_exact_keys(
            raw_entry,
            {"route_id", "spec_ref", "sha256"},
            set(),
            "frozen route registry entry",
        )
        route_id = entry["route_id"]
        if (
            not isinstance(route_id, str)
            or not root_writer.RUN_ID_RE.fullmatch(route_id)
            or route_id in route_entries
            or entry["spec_ref"] != f"routes/{route_id}.json"
            or not root_writer.SHA256_RE.fullmatch(str(entry["sha256"]))
        ):
            raise RunEngineError(f"frozen route registry entry is invalid: {route_id}")
        route_entries[route_id] = entry
    return role_entries, route_entries


def _frozen_artifact(
    workspace: Path,
    profile: dict[str, Any],
    run_id: str,
    ref: str,
    revision: str,
    expected_hash: str,
    label: str,
) -> RegistryArtifact:
    content = _state_bytes(workspace, profile, ref)
    assert content is not None
    actual_hash = _sha256(content)
    if actual_hash != expected_hash:
        raise RunEngineError(f"{label} hash differs from the frozen registry")
    _receipt_covering(workspace, profile, run_id, ref, content)
    return RegistryArtifact(
        ref=ref,
        revision=revision,
        sha256=actual_hash,
        path=workspace / profile["_state_root"] / ref,
        content=content,
        value=_json_object_bytes(content, label),
    )


def _route_for_manifest(
    workspace: Path,
    profile_path_or_profile: Path | dict[str, Any],
    profile_or_legacy_registry: dict[str, Any] | Any,
    manifest: dict[str, Any],
) -> (
    tuple[RouteContract, RouteContract, dict[str, RoleContract]]
    | RouteContract
):
    legacy_call = isinstance(profile_path_or_profile, dict)
    if legacy_call:
        profile = profile_path_or_profile
        profile_path = (
            workspace / profile["_state_root"].parent / "project.json"
        )
    else:
        profile_path = profile_path_or_profile
        profile = profile_or_legacy_registry
    if not isinstance(profile, dict):
        raise RunEngineError("selected project profile is invalid")
    run_id = manifest["run_id"]
    route_id = manifest["route_id"]
    role_entries, route_entries = _frozen_registry_entries(
        workspace, profile, manifest
    )
    route_entry = route_entries.get(route_id)
    if route_entry is None:
        raise RunEngineError("run route is absent from its frozen registry")

    try:
        route_pointer = root_writer._validate_pinned_reference(
            manifest["route_contract"], "manifest route_contract"
        )
    except root_writer.WriterError as exc:
        raise RunEngineError(str(exc)) from exc
    expected_route_ref = f"records/run-inputs/{run_id}/agents/route.json"
    if (
        route_pointer["artifact_ref"] != expected_route_ref
        or route_pointer["artifact_revision"] != route_id
        or route_pointer["artifact_sha256"] != route_entry["sha256"]
    ):
        raise RunEngineError("manifest route_contract is not its canonical frozen route")
    route_artifact = _frozen_artifact(
        workspace,
        profile,
        run_id,
        expected_route_ref,
        route_id,
        route_entry["sha256"],
        "frozen route contract",
    )

    raw_steps = manifest["planned_steps"]
    active_ids = {
        item.get("step_id")
        for item in raw_steps
        if isinstance(item, dict) and isinstance(item.get("step_id"), str)
    }
    if len(active_ids) != len(raw_steps):
        raise RunEngineError("run planned_steps contain duplicate or invalid identities")

    planned_role_ids = {
        item["role_id"]
        for item in raw_steps
        if isinstance(item, dict) and isinstance(item.get("role_id"), str)
    }
    frozen_roles: dict[str, RoleContract] = {}
    for role_id in sorted(planned_role_ids):
        entry = role_entries.get(role_id)
        if entry is None:
            raise RunEngineError(
                f"run role is absent from its frozen registry: {role_id}"
            )
        role_ref = f"records/run-inputs/{run_id}/agents/roles/{role_id}.json"
        artifact = _frozen_artifact(
            workspace,
            profile,
            run_id,
            role_ref,
            role_id,
            entry["sha256"],
            f"frozen role contract {role_id}",
        )
        try:
            frozen_roles[role_id] = role_contract_from_artifact(artifact, role_id)
        except AgentRegistryError as exc:
            raise RunEngineError(str(exc)) from exc

    try:
        base_route = route_contract_from_artifact(
            route_artifact,
            route_id,
            known_role_ids=set(role_entries),
            role_outputs={
                role_id: role.output_contracts
                for role_id, role in frozen_roles.items()
            },
            project_config_path=profile_path,
            allowed_roots=profile["_allowed_roots"],
            ownership_node_ids=active_ids,
        )
        route = route_with_active_nodes(base_route, active_ids)
    except AgentRegistryError as exc:
        raise RunEngineError(str(exc)) from exc
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
        raise RunEngineError("run manifest planned_steps differ from the frozen route")
    active_role_ids = {node.role_id for node in route.nodes}
    if set(frozen_roles) != active_role_ids:
        raise RunEngineError("run role snapshots differ from the frozen active route")
    if legacy_call:
        return route
    return base_route, route, frozen_roles


def _task_maps(
    manifest: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_step: dict[str, dict[str, Any]] = {}
    for task in manifest["tasks"]:
        if task["task_id"] in by_id or task["step_id"] in by_step:
            raise RunEngineError("run manifest contains duplicate task or step identities")
        by_id[task["task_id"]] = task
        by_step[task["step_id"]] = task
    return by_id, by_step


def _load_task(
    workspace: Path,
    profile_path: Path,
    profile: dict[str, Any],
    manifest: dict[str, Any],
    task_entry: dict[str, Any],
) -> tuple[dict[str, Any], bytes]:
    expected_ref = f"handoffs/{manifest['run_id']}/{task_entry['task_id']}.json"
    if task_entry["task_ref"] != expected_ref:
        raise RunEngineError("task manifest reference is not canonical for this run")
    content = _state_bytes(workspace, profile, expected_ref)
    assert content is not None
    if _sha256(content) != task_entry["task_sha256"]:
        raise RunEngineError("task bytes do not match the manifest task hash")
    task = _json_object_bytes(content, f"task {task_entry['task_id']}")
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
    ):
        raise RunEngineError("task identity or pinned run inputs do not match the manifest")
    _pinned_bytes(workspace, profile, task["role_contract"], "task role_contract")
    _receipt_covering(workspace, profile, manifest["run_id"], expected_ref, content)
    return task, content


def _pointer_identity(pointer: dict[str, Any]) -> tuple[str, str, str]:
    return (
        pointer["artifact_ref"],
        pointer["artifact_revision"],
        pointer["artifact_sha256"],
    )


def _result_artifact_pointers(
    result: dict[str, Any],
) -> tuple[
    dict[tuple[str, str, str], dict[str, Any]],
    dict[tuple[str, str, str], dict[str, Any]],
]:
    outputs: dict[tuple[str, str, str], dict[str, Any]] = {}
    evidence: dict[tuple[str, str, str], dict[str, Any]] = {}
    evidence_values: list[dict[str, Any]] = list(result["evidence_artifacts"])
    evidence_values.extend(
        check["evidence_ref"]
        for check in result["checks"]
        if check["evidence_ref"] is not None
    )
    by_ref: dict[str, tuple[str, str, str]] = {}
    for pointer in result["output_artifacts"]:
        identity = _pointer_identity(pointer)
        if identity in outputs:
            raise RunEngineError("result contains a duplicate output artifact pin")
        existing = by_ref.get(identity[0])
        if existing is not None and existing != identity:
            raise RunEngineError("result reuses one artifact_ref with conflicting pins")
        by_ref[identity[0]] = identity
        outputs.setdefault(identity, pointer)
    for pointer in evidence_values:
        identity = _pointer_identity(pointer)
        existing = by_ref.get(identity[0])
        if existing is not None and existing != identity:
            raise RunEngineError("result reuses one artifact_ref with conflicting pins")
        by_ref[identity[0]] = identity
        evidence.setdefault(identity, pointer)
    return outputs, evidence


def _inside_roots(raw_ref: str, roots: Iterable[str]) -> bool:
    try:
        path = root_writer._validate_relative_path(raw_ref, "result artifact_ref")
        prefixes = tuple(
            root_writer._validate_relative_path(root, "result write root")
            for root in roots
        )
    except root_writer.WriterError as exc:
        raise RunEngineError(str(exc)) from exc
    return any(path != prefix and root_writer._under_prefix(path, prefix) for prefix in prefixes)


def _validate_result_and_artifacts(
    workspace: Path,
    profile_path: Path,
    profile: dict[str, Any],
    manifest: dict[str, Any],
    route: RouteContract,
    node: RouteNode,
    task_entry: dict[str, Any],
    task: dict[str, Any],
    task_content: bytes,
    submission: dict[str, Any],
) -> tuple[dict[str, Any], bytes, list[dict[str, Any]]]:
    result = submission["result"]
    _validate_instance(profile_path, "result-envelope@2", result, "result envelope")
    if (
        result["project_id"] != profile["project_id"]
        or result["project_profile_revision"] != profile["profile_revision"]
        or result["run_id"] != manifest["run_id"]
        or result["task_id"] != task_entry["task_id"]
        or result["task_sha256"] != _sha256(task_content)
        or result["step_id"] != task_entry["step_id"]
        or result["role_id"] != task["assigned_role"]
        or result["role_id"] != node.role_id
    ):
        raise RunEngineError("result identity does not match the exact task envelope")
    if (
        result["provenance"]["contract_version"] != "result-envelope@2"
        or result["provenance"]["input_revision"] != task_entry["task_sha256"]
    ):
        raise RunEngineError("result provenance is not pinned to the exact task contract")
    if (
        task["output_contract"] != node.output_contract
        or task["output_contract"] != "result-envelope@2"
    ):
        raise RunEngineError("task output contract is not the route-pinned result envelope")
    if result["status"] == "completed" and not any(
        item["contract_id"] == node.deliverable_contract
        for item in result["output_artifacts"]
    ):
        raise RunEngineError(
            f"completed result lacks route deliverable {node.deliverable_contract}"
        )

    professional_bundle: ProfessionalContractBundle | None = None
    if result["status"] == "completed":
        if "_professional_contracts" not in profile:
            raise RunEngineError(
                "completed results require profile-pinned professional_contracts"
            )
        try:
            professional_bundle = load_professional_contracts(
                workspace, profile_path
            )
        except ProfessionalContractError as exc:
            raise RunEngineError(f"professional contract load failed: {exc}") from exc

    expected_outputs, evidence_pointers = _result_artifact_pointers(result)
    supplied: dict[tuple[str, str, str], dict[str, Any]] = {}
    supplied_content: dict[tuple[str, str, str], bytes] = {}
    supplied_refs: set[str] = set()
    writes: list[dict[str, Any]] = []
    for artifact in submission["artifacts"]:
        identity = _pointer_identity(artifact)
        if identity in supplied or identity[0] in supplied_refs:
            raise RunEngineError("result submission contains a duplicate artifact")
        supplied[identity] = artifact
        supplied_refs.add(identity[0])
        if not isinstance(artifact["media_type"], str) or not artifact["media_type"].strip():
            raise RunEngineError("result artifact media_type must be non-empty")
        if not isinstance(artifact["content"], str):
            raise RunEngineError("result artifact content must be a UTF-8 string")
        try:
            content = artifact["content"].encode("utf-8")
        except UnicodeEncodeError as exc:
            raise RunEngineError("result artifact content is not valid UTF-8") from exc
        supplied_content[identity] = content
        if _sha256(content) != artifact["artifact_sha256"]:
            raise RunEngineError(
                f"result artifact hash mismatch: {artifact['artifact_ref']}"
            )
        if not _inside_roots(artifact["artifact_ref"], task["authority"]["allowed_write_roots"]):
            raise RunEngineError(
                f"result artifact is outside task write roots: {artifact['artifact_ref']}"
            )
        if not _inside_roots(
            artifact["artifact_ref"],
            [prefix.as_posix() for prefix in profile["_allowed_roots"]],
        ):
            raise RunEngineError(
                f"result artifact is outside project write roots: {artifact['artifact_ref']}"
            )
        writes.append(
            _write_item(artifact["artifact_ref"], content, artifact["media_type"])
        )
    missing = sorted(set(expected_outputs) - set(supplied))
    extra = sorted(set(supplied) - set(expected_outputs) - set(evidence_pointers))
    if missing or extra:
        raise RunEngineError(
            "supplied artifacts do not match declared output/evidence pins; "
            f"missing={missing}, extra={extra}"
        )
    profile_roots = [prefix.as_posix() for prefix in profile["_allowed_roots"]]
    for identity, evidence in evidence_pointers.items():
        if identity in supplied:
            continue
        if not _inside_roots(evidence["artifact_ref"], profile_roots):
            raise RunEngineError(
                "result evidence is outside the selected project state roots: "
                f"{evidence['artifact_ref']}"
            )
        content = _state_bytes(workspace, profile, evidence["artifact_ref"])
        assert content is not None
        if _sha256(content) != evidence["artifact_sha256"]:
            raise RunEngineError(
                f"result evidence hash mismatch: {evidence['artifact_ref']}"
            )
        _receipt_covering_project_state(
            workspace,
            profile,
            manifest["run_id"],
            evidence["artifact_ref"],
            content,
        )
    if professional_bundle is not None:
        declared_evidence = {
            _pointer_identity(pointer) for pointer in evidence_pointers.values()
        }
        for identity, output in expected_outputs.items():
            if output["contract_id"] != node.deliverable_contract:
                raise RunEngineError(
                    "completed result output contract differs from the route deliverable"
                )
            try:
                validated = validate_deliverable_bytes(
                    professional_bundle,
                    supplied_content[identity],
                    expected_run_id=manifest["run_id"],
                    expected_task_id=task_entry["task_id"],
                    expected_task_sha256=_sha256(task_content),
                    expected_step_id=task_entry["step_id"],
                    expected_role_id=node.role_id,
                    expected_contract_id=node.deliverable_contract,
                    expected_artifact_revision=output["artifact_revision"],
                    pending_evidence={
                        pin: content for pin, content in supplied_content.items()
                        if pin in evidence_pointers
                    },
                )
            except ProfessionalContractError as exc:
                raise RunEngineError(
                    f"professional deliverable rejected: {exc}"
                ) from exc
            deliverable_evidence = {
                _pointer_identity(pointer)
                for pointer in validated.value["evidence_refs"]
            }
            if deliverable_evidence != declared_evidence:
                raise RunEngineError(
                    "professional deliverable evidence differs from result evidence pins"
                )
            if node.deliverable_contract == "governance-verdict@1":
                payload = validated.value["payload"]
                reviewed = payload["reviewed_artifact"]
                if payload["verdict"] != "pass":
                    raise RunEngineError("completed governance requires a pass verdict")
                # Exact equality to a schema-validated, hash-verified task pin
                # also admits short valid revisions (for example r1).
                if reviewed not in task["input_artifacts"]:
                    raise RunEngineError("governance must review an exact task input revision")
                candidate = submission.get("review_candidate")
                if candidate is not None and _pointer_identity(reviewed) != _pointer_identity(candidate):
                    raise RunEngineError("final governance must review the exact final product")
    result_content = _canonical_json(result)
    return result, result_content, writes


def _load_result(
    workspace: Path,
    profile_path: Path,
    profile: dict[str, Any],
    run_id: str,
    pointer: dict[str, Any],
) -> tuple[dict[str, Any], bytes]:
    content = _pinned_bytes(workspace, profile, pointer, "task result_ref")
    result = _json_object_bytes(content, "persisted result envelope")
    _validate_instance(profile_path, "result-envelope@2", result, "persisted result")
    _receipt_covering(workspace, profile, run_id, pointer["artifact_ref"], content)
    return result, content


def _enforce_operating_limits(
    workspace: Path,
    profile_path: Path,
    profile: dict[str, Any],
    manifest: dict[str, Any],
    plan: dict[str, Any],
    pending_result: dict[str, Any],
) -> None:
    """Enforce the plan budget across exact accepted results plus one candidate."""

    contract = plan.get("operating_contract")
    if contract is None:
        return
    accepted: list[dict[str, Any]] = []
    for task_entry in manifest["tasks"]:
        pointer = task_entry["result_ref"]
        if pointer is None:
            continue
        persisted, _ = _load_result(
            workspace,
            profile_path,
            profile,
            manifest["run_id"],
            pointer,
        )
        accepted.append(persisted)
    results = [*accepted, pending_result]
    source_refs = {
        pointer["artifact_ref"]
        for pointer in plan["diagnosis"]["evidence_refs"]
    }
    for result in results:
        source_refs.update(
            pointer["artifact_ref"] for pointer in result["evidence_artifacts"]
        )
        source_refs.update(
            check["evidence_ref"]["artifact_ref"]
            for check in result["checks"]
            if check["evidence_ref"] is not None
        )
    try:
        enforce_run_limits(contract, results, source_refs=source_refs)
    except OperatingContractError as exc:
        raise RunEngineError(f"operating contract rejected result: {exc}") from exc


def _plan_for_manifest(
    workspace: Path,
    profile_path: Path,
    profile: dict[str, Any],
    manifest: dict[str, Any],
    base_route: RouteContract | None = None,
    active_route: RouteContract | None = None,
) -> dict[str, Any]:
    content = _pinned_bytes(
        workspace, profile, manifest["coordinator_plan"], "manifest coordinator_plan"
    )
    _receipt_covering(
        workspace,
        profile,
        manifest["run_id"],
        manifest["coordinator_plan"]["artifact_ref"],
        content,
    )
    plan = _json_object_bytes(content, "coordinator plan")
    _validate_instance(profile_path, "coordinator-plan@1", plan, "coordinator plan")
    if (
        plan["project_id"] != profile["project_id"]
        or plan["project_profile_revision"] != profile["profile_revision"]
        or plan["run_mode"] != manifest["run_mode"]
        or plan["route_decision"]["selected_route_id"] != manifest["route_id"]
        or plan["objective"] != manifest["objective"]
    ):
        raise RunEngineError("coordinator plan does not match the run manifest")
    if base_route is not None and active_route is not None and "step_activation" in plan[
        "route_decision"
    ]:
        try:
            expected = route_for_activation_decisions(
                base_route,
                plan["route_decision"],
                require_step_activation=True,
            )
        except AgentRegistryError as exc:
            raise RunEngineError(str(exc)) from exc
        if [node.node_id for node in expected.nodes] != [
            node.node_id for node in active_route.nodes
        ]:
            raise RunEngineError(
                "coordinator plan step activation differs from the run manifest"
            )
    return plan


def _task_ids(
    plan: dict[str, Any], route: RouteContract, profile: dict[str, Any]
) -> dict[str, str]:
    identities = {
        node.node_id: f"{plan['work_item_id']}-T{index:03d}"
        for index, node in enumerate(route.nodes, start=1)
    }
    pattern = re.compile(profile["task_id_pattern"])
    if any(not pattern.fullmatch(task_id) for task_id in identities.values()):
        raise RunEngineError("route-generated task_id does not match the project profile")
    return identities


def _role_pointer(
    workspace: Path,
    profile: dict[str, Any],
    roles: dict[str, RoleContract],
    run_id: str,
    role_id: str,
) -> dict[str, str]:
    role = roles.get(role_id)
    if role is None:
        raise RunEngineError(f"frozen run role is missing: {role_id}")
    ref = f"records/run-inputs/{run_id}/agents/roles/{role_id}.json"
    if role.artifact.ref != ref or role.artifact.revision != role_id:
        raise RunEngineError(f"frozen run role pointer is not canonical: {role_id}")
    return _pointer(ref, role_id, role.artifact.content)


def _dependency_inputs(
    workspace: Path,
    profile_path: Path,
    profile: dict[str, Any],
    run_id: str,
    node: RouteNode,
    tasks_by_step: dict[str, dict[str, Any]],
    pending_results: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    pointers: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for dependency in node.depends_on:
        result = pending_results.get(dependency)
        if result is None:
            result_ref = tasks_by_step[dependency]["result_ref"]
            if result_ref is None:
                raise RunEngineError("new task dependency has no completed result")
            result, _ = _load_result(
                workspace, profile_path, profile, run_id, result_ref
            )
        raw_pointers = [*result["output_artifacts"], *result["evidence_artifacts"]]
        raw_pointers.extend(
            check["evidence_ref"]
            for check in result["checks"]
            if check["evidence_ref"] is not None
        )
        for raw in raw_pointers:
            pointer = {
                "artifact_ref": raw["artifact_ref"],
                "artifact_revision": raw["artifact_revision"],
                "artifact_sha256": raw["artifact_sha256"],
            }
            identity = _pointer_identity(pointer)
            if identity not in seen:
                pointers.append(pointer)
                seen.add(identity)
    return pointers


def _materialize_unblocked(
    workspace: Path,
    profile_path: Path,
    profile: dict[str, Any],
    roles: dict[str, RoleContract],
    route: RouteContract,
    plan: dict[str, Any],
    manifest: dict[str, Any],
    created_at: str,
    pending_results: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _, tasks_by_step = _task_maps(manifest)
    completed = {
        step_id for step_id, task in tasks_by_step.items() if task["status"] == "completed"
    }
    in_flight = sum(
        task["status"] in {"queued", "running"}
        for task in manifest["tasks"]
    )
    available_slots = route.max_concurrent_workhorses - in_flight
    if available_slots <= 0:
        return [], []
    identities = _task_ids(plan, route, profile)
    writes: list[dict[str, Any]] = []
    new_entries: list[dict[str, Any]] = []
    for node in route.nodes:
        if len(new_entries) >= available_slots:
            break
        if node.node_id in tasks_by_step or not set(node.depends_on).issubset(completed):
            continue
        role_pointer = _role_pointer(
            workspace, profile, roles, manifest["run_id"], node.role_id
        )
        inputs = [
            manifest["coordinator_plan"],
            manifest["project_context"],
            manifest["route_contract"],
            role_pointer,
            *_dependency_inputs(
                workspace,
                profile_path,
                profile,
                manifest["run_id"],
                node,
                tasks_by_step,
                pending_results,
            ),
        ]
        deduped: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for pointer in inputs:
            identity = _pointer_identity(pointer)
            if identity not in seen:
                deduped.append(pointer)
                seen.add(identity)
        task_id = identities[node.node_id]
        task = {
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
            "task_version": "2.0",
            "task_id": task_id,
            "run_id": manifest["run_id"],
            "step_id": node.node_id,
            "depends_on": [identities[item] for item in node.depends_on],
            "assigned_role": node.role_id,
            "role_contract": role_pointer,
            "route_id": route.route_id,
            "route_contract": manifest["route_contract"],
            "objective": f"{manifest['objective']}\n\nAssigned step: {node.objective}",
            "project_context": manifest["project_context"],
            "input_artifacts": deduped,
            "output_contract": node.output_contract,
            "authority": {
                "allowed_write_roots": list(node.allowed_write_roots),
                "external_side_effects": False,
                "may_publish": False,
            },
            "created_at": created_at,
        }
        _validate_instance(profile_path, "task-envelope@2", task, f"task {task_id}")
        content = _canonical_json(task)
        ref = f"handoffs/{manifest['run_id']}/{task_id}.json"
        writes.append(_write_item(ref, content))
        new_entries.append(
            {
                "step_id": node.node_id,
                "task_id": task_id,
                "task_ref": ref,
                "task_sha256": _sha256(content),
                "status": "queued",
                "result_ref": None,
            }
        )
    return writes, new_entries


def _append_outputs(manifest: dict[str, Any], result: dict[str, Any]) -> None:
    seen = {_pointer_identity(pointer) for pointer in manifest["output_artifacts"]}
    for output in result["output_artifacts"]:
        pointer = {
            "artifact_ref": output["artifact_ref"],
            "artifact_revision": output["artifact_revision"],
            "artifact_sha256": output["artifact_sha256"],
        }
        if _pointer_identity(pointer) not in seen:
            manifest["output_artifacts"].append(pointer)
            seen.add(_pointer_identity(pointer))


def _review_candidate(value: Any) -> dict[str, Any]:
    candidate = _require_exact_keys(
        value, REVIEW_REQUIRED, set(), "result submission review_candidate"
    )
    for field in (
        "artifact_ref",
        "artifact_revision",
        "artifact_sha256",
        "artifact_id",
        "title",
        "type",
        "preview",
    ):
        if not isinstance(candidate[field], str) or not candidate[field]:
            raise RunEngineError(f"review_candidate.{field} must be non-empty")
    if not isinstance(candidate["evidence"], list) or not isinstance(
        candidate["quality_checks"], list
    ):
        raise RunEngineError("review_candidate evidence and quality_checks must be arrays")
    return candidate


def _task_pointer(task_entry: dict[str, Any]) -> dict[str, str]:
    return {
        "artifact_ref": task_entry["task_ref"],
        "artifact_revision": task_entry["task_id"],
        "artifact_sha256": task_entry["task_sha256"],
    }


def _final_review_writes(
    workspace: Path,
    profile_path: Path,
    profile: dict[str, Any],
    route: RouteContract,
    manifest: dict[str, Any],
    tasks_by_step: dict[str, dict[str, Any]],
    governance_result_ref: dict[str, str],
    candidate_value: Any,
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    candidate = _review_candidate(candidate_value)
    producer_entry = tasks_by_step.get(route.final_product_node)
    governance_entry = tasks_by_step.get(route.governance_node)
    if producer_entry is None or governance_entry is None:
        raise RunEngineError("terminal route tasks are missing")
    producer_ref = producer_entry["result_ref"]
    if producer_ref is None:
        raise RunEngineError("final product producer has no completed result")
    producer_result, _ = _load_result(
        workspace, profile_path, profile, manifest["run_id"], producer_ref
    )
    matches = [
        output
        for output in producer_result["output_artifacts"]
        if output["contract_id"] == route.final_product_contract
        and _pointer_identity(output)
        == (
            candidate["artifact_ref"],
            candidate["artifact_revision"],
            candidate["artifact_sha256"],
        )
    ]
    if len(matches) != 1:
        raise RunEngineError(
            "review_candidate does not reference the route producer's exact final output"
        )
    source = _state_bytes(workspace, profile, candidate["artifact_ref"])
    assert source is not None
    if _sha256(source) != candidate["artifact_sha256"]:
        raise RunEngineError("review_candidate source bytes do not match its hash")
    _, source_receipt = _receipt_covering(
        workspace,
        profile,
        manifest["run_id"],
        candidate["artifact_ref"],
        source,
    )
    source_media_type = next(
        item["media_type"]
        for item in source_receipt["writes"]
        if item["path"] == candidate["artifact_ref"]
        and item["sha256"] == candidate["artifact_sha256"]
    )
    suffix = PurePosixPath(candidate["artifact_ref"]).suffix or ".txt"
    artifact_path = (
        f"outbox/artifacts/{candidate['artifact_id']}/"
        f"{candidate['artifact_revision']}{suffix}"
    )
    review_item = {
        "review_item_version": "1.0",
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "artifact_id": candidate["artifact_id"],
        "artifact_revision": candidate["artifact_revision"],
        "artifact_path": artifact_path,
        "artifact_sha256": _sha256(source),
        "title": candidate["title"],
        "type": candidate["type"],
        "preview": candidate["preview"],
        "evidence": candidate["evidence"],
        "quality_checks": candidate["quality_checks"],
    }
    _validate_instance(
        profile_path, "outbox-review-item@1", review_item, "outbox review item"
    )
    review_content = _canonical_json(review_item)
    review_ref = (
        f"outbox/pending/{candidate['artifact_id']}/"
        f"{candidate['artifact_revision']}.json"
    )
    review_pointer = _pointer(
        review_ref, candidate["artifact_revision"], review_content
    )
    lineage = {
        "lineage_version": "1.0",
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "artifact_id": candidate["artifact_id"],
        "artifact_revision": candidate["artifact_revision"],
        "artifact_sha256": candidate["artifact_sha256"],
        "source_run_id": manifest["run_id"],
        "source_route_id": route.route_id,
        "producer": {
            "step_id": route.final_product_node,
            "task_ref": _task_pointer(producer_entry),
            "result_ref": producer_ref,
            "artifact_ref": {
                "artifact_ref": candidate["artifact_ref"],
                "artifact_revision": candidate["artifact_revision"],
                "artifact_sha256": candidate["artifact_sha256"],
            },
        },
        "governance": {
            "step_id": route.governance_node,
            "task_ref": _task_pointer(governance_entry),
            "result_ref": governance_result_ref,
        },
        "review_manifest_ref": review_pointer,
    }
    lineage_content = _canonical_json(lineage)
    lineage_ref = (
        f"outbox/lineage/{candidate['artifact_id']}/"
        f"{candidate['artifact_revision']}.json"
    )
    lineage_pointer = _pointer(
        lineage_ref, candidate["artifact_revision"], lineage_content
    )
    return (
        [
            _write_item(artifact_path, source, source_media_type),
            _write_item(review_ref, review_content),
            _write_item(lineage_ref, lineage_content),
        ],
        review_pointer,
        lineage_pointer,
    )


def _verify_replay_artifacts(
    workspace: Path,
    profile: dict[str, Any],
    run_id: str,
    submission: dict[str, Any],
) -> None:
    for artifact in submission["artifacts"]:
        persisted = _state_bytes(workspace, profile, artifact["artifact_ref"])
        if persisted is None or persisted != artifact["content"].encode("utf-8"):
            raise RunEngineError("idempotent replay artifact bytes differ")
        _receipt_covering(
            workspace,
            profile,
            run_id,
            artifact["artifact_ref"],
            persisted,
            media_type=artifact["media_type"],
        )


def _replay_response(
    workspace: Path,
    profile: dict[str, Any],
    manifest: dict[str, Any],
    manifest_ref: str,
    manifest_content: bytes,
    task_entry: dict[str, Any],
    result_content: bytes,
    submission: dict[str, Any],
) -> dict[str, Any]:
    stored_ref = task_entry["result_ref"]
    assert stored_ref is not None
    stored = _pinned_bytes(workspace, profile, stored_ref, "terminal task result_ref")
    if stored != result_content:
        raise RunEngineError("task already has a different terminal result")
    _verify_replay_artifacts(
        workspace, profile, manifest["run_id"], submission
    )
    receipt_ref, receipt = _receipt_covering(
        workspace,
        profile,
        manifest["run_id"],
        stored_ref["artifact_ref"],
        stored,
    )
    return {
        "status": "accepted",
        "idempotent_replay": True,
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "run_id": manifest["run_id"],
        "task_id": task_entry["task_id"],
        "run_status": manifest["status"],
        "manifest": _pointer(
            manifest_ref, f"r{manifest['manifest_revision']:04d}", manifest_content
        ),
        "new_tasks": [],
        "review_item": manifest["review_items"][-1] if manifest["review_items"] else None,
        "lineage": None,
        "writer_receipt_ref": receipt_ref,
        "writer_receipt": receipt,
    }


def _apply_writes(
    workspace: Path,
    profile_path: Path,
    profile: dict[str, Any],
    run_id: str,
    task_id: str,
    writes: list[dict[str, Any]],
) -> dict[str, Any]:
    writer_key = str(
        uuid.uuid5(
            WRITER_NAMESPACE,
            "\n".join(
                [profile["project_id"], profile["profile_revision"], run_id, task_id]
            ),
        )
    )
    request = {
        "writer_request_version": "1.0",
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "run_id": run_id,
        "idempotency_key": writer_key,
        "requested_by": profile["root_role_id"],
        "writes": writes,
    }
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix="growth-run-engine-", suffix=".json", delete=False
        ) as handle:
            handle.write(_canonical_json(request))
            request_path = Path(handle.name)
        try:
            receipt = root_writer.apply_request(workspace, profile_path, request_path)
        finally:
            request_path.unlink(missing_ok=True)
    except (OSError, root_writer.WriterError) as exc:
        raise RunEngineError(str(exc)) from exc
    expected = {item["path"] for item in writes}
    actual = {item["path"] for item in receipt["writes"]}
    if expected != actual:
        raise RunEngineError("completed Root Writer receipt does not cover the result transaction")
    return receipt


def accept_result(
    workspace: Path,
    profile_path: Path,
    submission_path: Path,
) -> dict[str, Any]:
    """Verify one role submission, persist it, and advance its exact route DAG."""

    submission = _submission(submission_path)
    workspace, profile = _load_profile(workspace, profile_path)
    if (
        submission["project_id"] != profile["project_id"]
        or submission["project_profile_revision"] != profile["profile_revision"]
    ):
        raise RunEngineError("result submission belongs to another project profile")
    run_id = submission["run_id"]
    task_id = submission["task_id"]
    if not isinstance(run_id, str) or not root_writer.RUN_ID_RE.fullmatch(run_id):
        raise RunEngineError("result submission run_id is invalid")
    if not isinstance(task_id, str) or not root_writer.RUN_ID_RE.fullmatch(task_id):
        raise RunEngineError("result submission task_id is invalid")

    with _run_lock(workspace, run_id):
        manifest, manifest_ref, manifest_content = _latest_manifest(
            workspace, profile_path, profile, run_id
        )
        if manifest["project_id"] != submission["project_id"]:
            raise RunEngineError("run belongs to another project")
        base_route, route, frozen_roles = _route_for_manifest(
            workspace, profile_path, profile, manifest
        )
        plan = _plan_for_manifest(
            workspace,
            profile_path,
            profile,
            manifest,
            base_route,
            route,
        )
        if "step_activation" in plan["route_decision"] and sum(
            item["status"] in {"queued", "running"}
            for item in manifest["tasks"]
        ) > route.max_concurrent_workhorses:
            raise RunEngineError("run exceeds the route workhorse concurrency cap")
        tasks_by_id, _ = _task_maps(manifest)
        task_entry = tasks_by_id.get(task_id)
        if task_entry is None:
            raise RunEngineError("submitted task is not materialized in the latest manifest")
        node = next(
            (item for item in route.nodes if item.node_id == task_entry["step_id"]),
            None,
        )
        if node is None:
            raise RunEngineError("submitted task step is absent from the pinned route")
        task, task_content = _load_task(
            workspace, profile_path, profile, manifest, task_entry
        )
        expected_role = frozen_roles[node.role_id]
        expected_role_pointer = _pointer(
            expected_role.artifact.ref,
            expected_role.artifact.revision,
            expected_role.artifact.content,
        )
        if (
            task["assigned_role"] != node.role_id
            or task["role_contract"] != expected_role_pointer
            or task["output_contract"] != node.output_contract
        ):
            raise RunEngineError(
                "task assignment differs from the frozen route and role contract"
            )
        result, result_content, artifact_writes = _validate_result_and_artifacts(
            workspace,
            profile_path,
            profile,
            manifest,
            route,
            node,
            task_entry,
            task,
            task_content,
            submission,
        )
        is_governance = node.node_id == route.governance_node
        if is_governance and result["status"] == "completed":
            if "review_candidate" not in submission:
                raise RunEngineError("completed final governance requires review_candidate")
        elif "review_candidate" in submission:
            raise RunEngineError(
                "review_candidate is only allowed for completed final governance"
            )

        if task_entry["status"] in TERMINAL_TASK_STATUSES:
            return _replay_response(
                workspace,
                profile,
                manifest,
                manifest_ref,
                manifest_content,
                task_entry,
                result_content,
                submission,
            )
        if task_entry["status"] not in {"queued", "running"}:
            raise RunEngineError("submitted task is not in an acceptable state")
        if manifest["status"] not in {"queued", "running"}:
            raise RunEngineError("run is terminal and cannot accept another task result")

        _enforce_operating_limits(
            workspace,
            profile_path,
            profile,
            manifest,
            plan,
            result,
        )

        result_ref = f"handoffs/{run_id}/results/{task_id}.json"
        result_pointer = _pointer(
            result_ref, f"attempt-{result['attempts']}", result_content
        )
        writes = [*artifact_writes, _write_item(result_ref, result_content)]
        next_manifest = copy.deepcopy(manifest)
        for entry in next_manifest["tasks"]:
            if entry["task_id"] == task_id:
                entry["status"] = result["status"]
                entry["result_ref"] = result_pointer
                break
        _append_outputs(next_manifest, result)

        new_tasks: list[dict[str, Any]] = []
        review_pointer: dict[str, str] | None = None
        lineage_pointer: dict[str, str] | None = None
        if result["status"] != "completed":
            next_manifest["status"] = (
                "failed" if result["status"] == "failed" else "blocked"
            )
            next_manifest["blocked_reason"] = result["error"]["message"]
            next_manifest["completed_at"] = result["created_at"]
        else:
            task_writes, new_tasks = _materialize_unblocked(
                workspace,
                profile_path,
                profile,
                frozen_roles,
                route,
                plan,
                next_manifest,
                result["created_at"],
                {node.node_id: result},
            )
            writes.extend(task_writes)
            next_manifest["tasks"].extend(new_tasks)
            if is_governance:
                _, next_by_step = _task_maps(next_manifest)
                if set(next_by_step) != {node.node_id for node in route.nodes} or any(
                    entry["status"] != "completed" for entry in next_manifest["tasks"]
                ):
                    raise RunEngineError(
                        "final governance cannot close before every planned route node completes"
                    )
                review_writes, review_pointer, lineage_pointer = _final_review_writes(
                    workspace,
                    profile_path,
                    profile,
                    route,
                    next_manifest,
                    next_by_step,
                    result_pointer,
                    submission["review_candidate"],
                )
                writes.extend(review_writes)
                next_manifest["review_items"].append(review_pointer)
                next_manifest["status"] = "awaiting_review"
            else:
                if not new_tasks and all(
                    entry["status"] == "completed"
                    for entry in next_manifest["tasks"]
                ):
                    raise RunEngineError("completed route cannot advance to final governance")
                next_manifest["status"] = "running"
            next_manifest["blocked_reason"] = None
            next_manifest["completed_at"] = None

        next_revision = manifest["manifest_revision"] + 1
        next_manifest["manifest_revision"] = next_revision
        next_manifest["previous_manifest"] = _pointer(
            manifest_ref,
            f"r{manifest['manifest_revision']:04d}",
            manifest_content,
        )
        if "step_activation" in plan["route_decision"] and sum(
            item["status"] in {"queued", "running"}
            for item in next_manifest["tasks"]
        ) > route.max_concurrent_workhorses:
            raise RunEngineError("advanced run exceeds the route workhorse concurrency cap")
        _validate_instance(
            profile_path, "run-manifest@2", next_manifest, "advanced run manifest"
        )
        next_content = _canonical_json(next_manifest)
        next_ref = f"records/runs/{run_id}/manifest-r{next_revision:04d}.json"
        writes.append(_write_item(next_ref, next_content))
        receipt = _apply_writes(
            workspace, profile_path, profile, run_id, task_id, writes
        )
        return {
            "status": "accepted",
            "idempotent_replay": False,
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
            "run_id": run_id,
            "task_id": task_id,
            "run_status": next_manifest["status"],
            "manifest": _pointer(
                next_ref, f"r{next_revision:04d}", next_content
            ),
            "new_tasks": [
                {
                    "step_id": entry["step_id"],
                    "task_id": entry["task_id"],
                    "task_ref": entry["task_ref"],
                    "task_sha256": entry["task_sha256"],
                }
                for entry in new_tasks
            ],
            "review_item": review_pointer,
            "lineage": lineage_pointer,
            "writer_receipt": receipt,
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    accept = subcommands.add_parser(
        "accept", help="validate one workhorse result and advance its route"
    )
    accept.add_argument("--workspace", required=True, type=Path)
    accept.add_argument("--project-config", required=True, type=Path)
    accept.add_argument("--submission", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "accept":
            response = accept_result(
                args.workspace, args.project_config, args.submission
            )
        else:  # pragma: no cover - argparse prevents this branch.
            raise RunEngineError(f"unsupported command: {args.command}")
    except RunEngineError as exc:
        print(
            json.dumps({"status": "rejected", "error": str(exc)}),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(response, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
