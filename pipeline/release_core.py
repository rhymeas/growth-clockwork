#!/usr/bin/env python3
"""Approved-only, project-neutral release-package boundary.

This module turns one exact human ``approve`` action into an immutable,
credential-free release package.  It does not select or call a publisher.  It
never reaches the network, invokes a shell, or creates an external side effect.

Every input is read below one selected project state root.  The pending review
manifest, reviewed bytes, lineage, origin bytes, human action, and their
completed Root Writer receipts must agree before a package can be written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any
import uuid

from pipeline import root_writer
from pipeline.review_api import ACTION_NAMESPACE, validate_pending_manifest_boundary
from pipeline.validate_json_suites import (
    SuiteConfigurationError,
    validate_registered_instance,
)


RELEASE_NAMESPACE = uuid.UUID("e6d00fd0-86f4-4b09-aae5-a3619311e2da")
RECEIPT_NAME_RE = re.compile(
    r"^writer-([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\.json$"
)
RFC3339_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
PORTABLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ReleaseError(ValueError):
    """Fail-closed approval, lineage, receipt, or package error."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _json_bytes(content: bytes, label: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-JSON numeric constant {value}")

    try:
        value = json.loads(content.decode("utf-8"), parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReleaseError(f"cannot parse {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseError(f"{label} must be a JSON object")
    return value


def _exact_keys(
    value: Any, required: set[str], optional: set[str], label: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseError(f"{label} must be an object")
    missing = sorted(required - value.keys())
    extra = sorted(value.keys() - required - optional)
    if missing:
        raise ReleaseError(f"{label} missing fields: {', '.join(missing)}")
    if extra:
        raise ReleaseError(f"{label} has unknown fields: {', '.join(extra)}")
    return value


def _safe_ref(raw: Any, label: str, prefix: str | None = None) -> PurePosixPath:
    try:
        relative = root_writer._validate_relative_path(raw, label)
    except root_writer.WriterError as exc:
        raise ReleaseError(str(exc)) from exc
    if prefix is not None:
        expected = PurePosixPath(prefix)
        if relative == expected or not root_writer._under_prefix(relative, expected):
            raise ReleaseError(f"{label} must name a file below {prefix}")
    return relative


def _pointer(
    artifact_ref: str, artifact_revision: str, content: bytes
) -> dict[str, str]:
    return {
        "artifact_ref": artifact_ref,
        "artifact_revision": artifact_revision,
        "artifact_sha256": _sha256(content),
    }


def _validate_pointer(value: Any, label: str) -> dict[str, str]:
    pointer = _exact_keys(
        value,
        {"artifact_ref", "artifact_revision", "artifact_sha256"},
        set(),
        label,
    )
    relative = _safe_ref(pointer["artifact_ref"], f"{label}.artifact_ref")
    revision = pointer["artifact_revision"]
    digest = pointer["artifact_sha256"]
    if not isinstance(revision, str) or not PORTABLE_ID_RE.fullmatch(revision):
        raise ReleaseError(f"{label}.artifact_revision is invalid")
    if not isinstance(digest, str) or not root_writer.SHA256_RE.fullmatch(digest):
        raise ReleaseError(f"{label}.artifact_sha256 is invalid")
    return {
        "artifact_ref": relative.as_posix(),
        "artifact_revision": revision,
        "artifact_sha256": digest,
    }


def _load_profile(
    workspace_path: Path, profile_path: Path
) -> tuple[Path, Path, dict[str, Any]]:
    try:
        workspace = root_writer._validate_workspace(workspace_path)
        profile = root_writer.load_project_profile(profile_path)
        root_writer._validate_profile_package(workspace, profile_path, profile)
        resolved_profile = profile_path.resolve(strict=True)
    except (OSError, root_writer.WriterError) as exc:
        raise ReleaseError(str(exc)) from exc
    return workspace, resolved_profile, profile


def _state_bytes(
    workspace: Path,
    profile: dict[str, Any],
    relative: PurePosixPath | str,
    *,
    missing_ok: bool = False,
) -> bytes | None:
    relative = (
        relative
        if isinstance(relative, PurePosixPath)
        else _safe_ref(relative, "state artifact_ref")
    )
    state_root = workspace.joinpath(*profile["_state_root"].parts)
    try:
        state_fd = os.open(state_root, root_writer._directory_flags())
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ReleaseError("selected project state does not exist")
    except OSError as exc:
        raise ReleaseError(f"cannot open selected project state: {exc}") from exc
    try:
        try:
            return root_writer._read_relative_bytes(
                state_fd, relative, missing_ok=missing_ok
            )
        except root_writer.WriterError as exc:
            raise ReleaseError(str(exc)) from exc
    finally:
        os.close(state_fd)


def _validate_schema(
    profile_path: Path, schema_id: str, value: dict[str, Any], label: str
) -> None:
    try:
        errors = validate_registered_instance(profile_path, schema_id, value)
    except SuiteConfigurationError as exc:
        raise ReleaseError(f"cannot validate {label}: {exc}") from exc
    if errors:
        first = errors[0]
        raise ReleaseError(
            f"{label} violates {schema_id}: {first.instance_path} {first.rule}"
        )


def _validate_receipt(
    workspace: Path,
    profile: dict[str, Any],
    receipt_ref: PurePosixPath,
    content: bytes,
    expected_run_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    receipt = _json_bytes(content, f"Root Writer receipt {receipt_ref}")
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
    _exact_keys(receipt, required, set(), "Root Writer receipt")
    if receipt["receipt_version"] != "1.0" or receipt["status"] != "completed":
        raise ReleaseError("Root Writer receipt is not completed v1")
    for field, expected in (
        ("project_id", profile["project_id"]),
        ("project_profile_revision", profile["profile_revision"]),
        ("run_id", expected_run_id),
        ("requested_by", profile["root_role_id"]),
    ):
        if receipt[field] != expected:
            raise ReleaseError(f"Root Writer receipt identity mismatch: {field}")
    try:
        parsed_key = uuid.UUID(str(receipt["idempotency_key"]))
    except (ValueError, AttributeError) as exc:
        raise ReleaseError("Root Writer receipt idempotency key is invalid") from exc
    if str(parsed_key) != receipt["idempotency_key"]:
        raise ReleaseError("Root Writer receipt idempotency key is not canonical")
    if receipt_ref.name != f"writer-{receipt['idempotency_key']}.json":
        raise ReleaseError("Root Writer receipt filename does not match idempotency key")
    if not root_writer.SHA256_RE.fullmatch(str(receipt["request_sha256"])):
        raise ReleaseError("Root Writer receipt request hash is invalid")
    if not isinstance(receipt["created_at"], str) or not RFC3339_RE.fullmatch(
        receipt["created_at"]
    ):
        raise ReleaseError("Root Writer receipt created_at is invalid")
    writes = receipt["writes"]
    if not isinstance(writes, list) or not writes:
        raise ReleaseError("Root Writer receipt has no writes")
    checked: list[dict[str, Any]] = []
    reconstructed_writes: list[dict[str, Any]] = []
    for index, item in enumerate(writes):
        _exact_keys(
            item,
            {"path", "mode", "sha256", "bytes", "media_type"},
            set(),
            f"Root Writer receipt writes[{index}]",
        )
        relative = _safe_ref(item["path"], f"receipt writes[{index}].path")
        if item["mode"] != "create":
            raise ReleaseError("Root Writer receipt contains a non-create write")
        if not root_writer.SHA256_RE.fullmatch(str(item["sha256"])):
            raise ReleaseError("Root Writer receipt contains an invalid write hash")
        if (
            not isinstance(item["bytes"], int)
            or isinstance(item["bytes"], bool)
            or item["bytes"] < 0
        ):
            raise ReleaseError("Root Writer receipt contains an invalid byte count")
        if not isinstance(item["media_type"], str) or not item["media_type"]:
            raise ReleaseError("Root Writer receipt contains an invalid media type")
        persisted = _state_bytes(workspace, profile, relative)
        assert persisted is not None
        if _sha256(persisted) != item["sha256"] or len(persisted) != item["bytes"]:
            raise ReleaseError(
                f"Root Writer receipt readback mismatch: {relative.as_posix()}"
            )
        checked.append({**item, "path": relative.as_posix()})
        try:
            decoded = persisted.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReleaseError("Root Writer v1 receipt covers non-UTF-8 bytes") from exc
        reconstructed_writes.append(
            {
                "path": relative.as_posix(),
                "mode": "create",
                "content": decoded,
                "content_sha256": item["sha256"],
                "expected_sha256": None,
                "media_type": item["media_type"],
            }
        )
    reconstructed_request = {
        "writer_request_version": root_writer.REQUEST_VERSION,
        "project_id": receipt["project_id"],
        "project_profile_revision": receipt["project_profile_revision"],
        "run_id": receipt["run_id"],
        "idempotency_key": receipt["idempotency_key"],
        "requested_by": receipt["requested_by"],
        "writes": reconstructed_writes,
    }
    if _sha256(root_writer._canonical_json(reconstructed_request)) != receipt[
        "request_sha256"
    ]:
        raise ReleaseError("Root Writer receipt request hash cannot be reconstructed")
    return receipt, checked


def _receipt_covering(
    workspace: Path,
    profile: dict[str, Any],
    run_id: str,
    artifact_ref: str,
    content: bytes,
) -> tuple[dict[str, str], dict[str, Any], dict[str, Any]]:
    if not isinstance(run_id, str) or not root_writer.RUN_ID_RE.fullmatch(run_id):
        raise ReleaseError("receipt run_id is invalid")
    relative_dir = profile["_receipt_root"] / run_id
    state_root = workspace.joinpath(*profile["_state_root"].parts)
    try:
        state_fd = os.open(state_root, root_writer._directory_flags())
    except OSError as exc:
        raise ReleaseError(f"cannot open selected project state: {exc}") from exc
    try:
        try:
            directory_fd = root_writer._open_directory_chain(
                state_fd,
                relative_dir.parts,
                create=False,
                label=f"receipt directory {run_id}",
            )
        except (FileNotFoundError, root_writer.WriterError) as exc:
            raise ReleaseError(
                f"completed Root Writer receipt directory is missing: {run_id}"
            ) from exc
        try:
            names = sorted(os.listdir(directory_fd))
        finally:
            os.close(directory_fd)
    finally:
        os.close(state_fd)
    expected_hash = _sha256(content)
    for name in names:
        if not RECEIPT_NAME_RE.fullmatch(name):
            raise ReleaseError(f"unsafe file in Root Writer receipt directory: {name}")
        receipt_ref = relative_dir / name
        receipt_bytes = _state_bytes(workspace, profile, receipt_ref)
        assert receipt_bytes is not None
        receipt, writes = _validate_receipt(
            workspace, profile, receipt_ref, receipt_bytes, run_id
        )
        for item in writes:
            if (
                item["path"] == artifact_ref
                and item["sha256"] == expected_hash
                and item["bytes"] == len(content)
            ):
                return (
                    _pointer(receipt_ref.as_posix(), run_id, receipt_bytes),
                    receipt,
                    item,
                )
    raise ReleaseError(
        f"no completed Root Writer receipt covers exact bytes for {artifact_ref}"
    )


def _approval_run_id(action_id: str) -> str:
    return f"review-{action_id.replace('-', '')[:20]}"


def _approved_bundle(
    workspace: Path,
    profile_path: Path,
    profile: dict[str, Any],
    artifact_id: str,
    artifact_revision: str,
    artifact_sha256: str,
) -> dict[str, Any]:
    for value, label in (
        (artifact_id, "artifact_id"),
        (artifact_revision, "artifact_revision"),
    ):
        if not isinstance(value, str) or not PORTABLE_ID_RE.fullmatch(value):
            raise ReleaseError(f"{label} is invalid")
    if not isinstance(artifact_sha256, str) or not root_writer.SHA256_RE.fullmatch(
        artifact_sha256
    ):
        raise ReleaseError("artifact_sha256 is invalid")

    review_ref = PurePosixPath("outbox/pending") / artifact_id / (
        f"{artifact_revision}.json"
    )
    review_bytes = _state_bytes(workspace, profile, review_ref)
    assert review_bytes is not None
    try:
        review = validate_pending_manifest_boundary(
            _json_bytes(review_bytes, "pending review manifest"), profile
        )
    except Exception as exc:
        if isinstance(exc, ReleaseError):
            raise
        raise ReleaseError(f"invalid pending review manifest: {exc}") from exc
    _validate_schema(profile_path, "outbox-review-item@1", review, "review manifest")
    if (
        review["artifact_id"] != artifact_id
        or review["artifact_revision"] != artifact_revision
        or review["artifact_sha256"] != artifact_sha256
    ):
        raise ReleaseError("requested artifact identity does not match pending review")

    artifact_ref = _safe_ref(
        review["artifact_path"], "review artifact_path", "outbox/artifacts"
    )
    artifact_bytes = _state_bytes(workspace, profile, artifact_ref)
    assert artifact_bytes is not None
    if _sha256(artifact_bytes) != artifact_sha256:
        raise ReleaseError("reviewed artifact bytes do not match the approved hash")

    lineage_ref = PurePosixPath("outbox/lineage") / artifact_id / (
        f"{artifact_revision}.json"
    )
    lineage_bytes = _state_bytes(workspace, profile, lineage_ref)
    assert lineage_bytes is not None
    lineage = _json_bytes(lineage_bytes, "review lineage")
    _exact_keys(
        lineage,
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
        set(),
        "review lineage",
    )
    if lineage["lineage_version"] != "1.0":
        raise ReleaseError("unsupported review lineage version")
    for field, expected in (
        ("project_id", profile["project_id"]),
        ("project_profile_revision", profile["profile_revision"]),
        ("artifact_id", artifact_id),
        ("artifact_revision", artifact_revision),
        ("artifact_sha256", artifact_sha256),
    ):
        if lineage[field] != expected:
            raise ReleaseError(f"review lineage identity mismatch: {field}")
    source_run_id = lineage["source_run_id"]
    if not isinstance(source_run_id, str) or not root_writer.RUN_ID_RE.fullmatch(
        source_run_id
    ):
        raise ReleaseError("review lineage source_run_id is invalid")
    if not isinstance(lineage["source_route_id"], str) or not lineage[
        "source_route_id"
    ]:
        raise ReleaseError("review lineage source_route_id is invalid")
    expected_review_pointer = _pointer(
        review_ref.as_posix(), artifact_revision, review_bytes
    )
    if _validate_pointer(
        lineage["review_manifest_ref"], "lineage.review_manifest_ref"
    ) != expected_review_pointer:
        raise ReleaseError("lineage does not pin the exact pending review manifest")
    producer = _exact_keys(
        lineage["producer"],
        {"step_id", "task_ref", "result_ref", "artifact_ref"},
        set(),
        "lineage.producer",
    )
    governance = _exact_keys(
        lineage["governance"],
        {"step_id", "task_ref", "result_ref"},
        set(),
        "lineage.governance",
    )
    if not isinstance(producer["step_id"], str) or not producer["step_id"]:
        raise ReleaseError("lineage producer step_id is invalid")
    if not isinstance(governance["step_id"], str) or not governance["step_id"]:
        raise ReleaseError("lineage governance step_id is invalid")
    for label, value in (
        ("lineage.producer.task_ref", producer["task_ref"]),
        ("lineage.producer.result_ref", producer["result_ref"]),
        ("lineage.governance.task_ref", governance["task_ref"]),
        ("lineage.governance.result_ref", governance["result_ref"]),
    ):
        _validate_pointer(value, label)
    origin_pointer = _validate_pointer(
        producer["artifact_ref"], "lineage.producer.artifact_ref"
    )
    origin_bytes = _state_bytes(workspace, profile, origin_pointer["artifact_ref"])
    assert origin_bytes is not None
    if (
        _sha256(origin_bytes) != origin_pointer["artifact_sha256"]
        or origin_bytes != artifact_bytes
        or origin_pointer["artifact_revision"] != artifact_revision
    ):
        raise ReleaseError("lineage origin does not match the exact reviewed bytes")

    review_receipt, _, artifact_receipt_item = _receipt_covering(
        workspace, profile, source_run_id, artifact_ref.as_posix(), artifact_bytes
    )
    for ref, content in (
        (review_ref.as_posix(), review_bytes),
        (lineage_ref.as_posix(), lineage_bytes),
    ):
        pointer, _, _ = _receipt_covering(
            workspace, profile, source_run_id, ref, content
        )
        if pointer != review_receipt:
            raise ReleaseError(
                "review artifact, pending manifest, and lineage require one completed transaction receipt"
            )
    _receipt_covering(
        workspace,
        profile,
        source_run_id,
        origin_pointer["artifact_ref"],
        origin_bytes,
    )

    action_ref = PurePosixPath("outbox/review-actions") / artifact_id / (
        f"{artifact_revision}.json"
    )
    action_bytes = _state_bytes(workspace, profile, action_ref)
    assert action_bytes is not None
    action = _json_bytes(action_bytes, "human review action")
    _validate_schema(profile_path, "human-review-action@1", action, "human action")
    for field, expected in (
        ("project_id", profile["project_id"]),
        ("project_profile_revision", profile["profile_revision"]),
        ("artifact_id", artifact_id),
        ("artifact_revision", artifact_revision),
        ("artifact_sha256", artifact_sha256),
    ):
        if action.get(field) != expected:
            raise ReleaseError(f"human review action identity mismatch: {field}")
    if action.get("action") != "approve":
        raise ReleaseError("only an exact human approve action can create a release package")
    if action.get("actor_type") != "human":
        raise ReleaseError("release approval actor_type must be human")
    expected_identity = "\n".join(
        [
            profile["project_id"],
            profile["profile_revision"],
            artifact_id,
            artifact_revision,
            artifact_sha256,
        ]
    )
    expected_action_id = str(uuid.uuid5(ACTION_NAMESPACE, expected_identity))
    if action.get("action_id") != expected_action_id:
        raise ReleaseError("human approval action_id does not match exact artifact identity")
    approval_run_id = _approval_run_id(expected_action_id)
    approval_receipt, _, _ = _receipt_covering(
        workspace, profile, approval_run_id, action_ref.as_posix(), action_bytes
    )
    return {
        "review": review,
        "review_ref": review_ref.as_posix(),
        "review_bytes": review_bytes,
        "artifact_ref": artifact_ref.as_posix(),
        "artifact_bytes": artifact_bytes,
        "artifact_media_type": artifact_receipt_item["media_type"],
        "lineage": lineage,
        "lineage_ref": lineage_ref.as_posix(),
        "lineage_bytes": lineage_bytes,
        "origin_pointer": origin_pointer,
        "action": action,
        "action_ref": action_ref.as_posix(),
        "action_bytes": action_bytes,
        "source_run_id": source_run_id,
        "review_receipt": review_receipt,
        "approval_receipt": approval_receipt,
    }


def _writer_request(
    profile: dict[str, Any], run_id: str, idempotency_key: str, writes: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "writer_request_version": root_writer.REQUEST_VERSION,
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "run_id": run_id,
        "idempotency_key": idempotency_key,
        "requested_by": profile["root_role_id"],
        "writes": writes,
    }


def _write_item(path: str, content: bytes, media_type: str = "application/json") -> dict[str, Any]:
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseError("Root Writer v1 release outputs must be UTF-8") from exc
    return {
        "path": path,
        "mode": "create",
        "content": decoded,
        "content_sha256": _sha256(content),
        "expected_sha256": None,
        "media_type": media_type,
    }


def _apply_request(
    workspace: Path,
    profile_path: Path,
    request: dict[str, Any],
) -> dict[str, Any]:
    request_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix="growth-release-", suffix=".json", delete=False
        ) as handle:
            handle.write(json.dumps(request, ensure_ascii=False).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
            request_path = Path(handle.name)
        return root_writer.apply_request(workspace, profile_path, request_path)
    except (OSError, root_writer.WriterError) as exc:
        raise ReleaseError(str(exc)) from exc
    finally:
        if request_path is not None:
            request_path.unlink(missing_ok=True)


def create_release_package(
    workspace_path: Path,
    profile_path: Path,
    *,
    artifact_id: str,
    artifact_revision: str,
    artifact_sha256: str,
) -> dict[str, Any]:
    """Create or replay one immutable release package for exact approved bytes."""

    workspace, profile_path, profile = _load_profile(workspace_path, profile_path)
    bundle = _approved_bundle(
        workspace,
        profile_path,
        profile,
        artifact_id,
        artifact_revision,
        artifact_sha256,
    )
    package = create_package_value(profile, bundle)
    release_id = package["release_id"]
    _validate_schema(profile_path, "release-package@1", package, "release package")
    package_content = _canonical_json(package)
    package_ref = f"records/release-packages/{artifact_id}/{release_id}.json"
    release_run_id = f"release-{release_id.replace('-', '')[:24]}"
    existed = _state_bytes(workspace, profile, package_ref, missing_ok=True)
    request = _writer_request(
        profile,
        release_run_id,
        release_id,
        [_write_item(package_ref, package_content)],
    )
    receipt = _apply_request(workspace, profile_path, request)
    return {
        "status": "packaged",
        "idempotent_replay": existed == package_content,
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "release_package": _pointer(package_ref, release_id, package_content),
        "package": package,
        "writer_receipt": receipt,
    }


def load_verified_release_package(
    workspace_path: Path,
    profile_path: Path,
    package_pointer: dict[str, str],
) -> dict[str, Any]:
    """Re-verify a package and its entire approved source chain for an adapter."""

    workspace, profile_path, profile = _load_profile(workspace_path, profile_path)
    pointer = _validate_pointer(package_pointer, "release_package")
    _safe_ref(pointer["artifact_ref"], "release_package.artifact_ref", "records/release-packages")
    content = _state_bytes(workspace, profile, pointer["artifact_ref"])
    assert content is not None
    if _sha256(content) != pointer["artifact_sha256"]:
        raise ReleaseError("release package bytes do not match the selected hash")
    package = _json_bytes(content, "release package")
    _validate_schema(profile_path, "release-package@1", package, "release package")
    if pointer["artifact_revision"] != package["release_id"]:
        raise ReleaseError("release package pointer revision does not match release_id")
    if (
        package["project_id"] != profile["project_id"]
        or package["project_profile_revision"] != profile["profile_revision"]
    ):
        raise ReleaseError("release package belongs to another project profile")
    expected_ref = f"records/release-packages/{package['artifact_id']}/{package['release_id']}.json"
    if pointer["artifact_ref"] != expected_ref:
        raise ReleaseError("release package path does not match its immutable identity")
    release_run_id = f"release-{package['release_id'].replace('-', '')[:24]}"
    package_receipt, _, _ = _receipt_covering(
        workspace, profile, release_run_id, expected_ref, content
    )
    bundle = _approved_bundle(
        workspace,
        profile_path,
        profile,
        package["artifact_id"],
        package["artifact_revision"],
        package["artifact_sha256"],
    )
    expected = create_package_value(profile, bundle)
    if package != expected:
        raise ReleaseError("release package no longer matches its exact approved source chain")
    return {
        "workspace": workspace,
        "profile_path": profile_path,
        "profile": profile,
        "pointer": pointer,
        "content": content,
        "package": package,
        "bundle": bundle,
        "package_receipt": package_receipt,
    }


def create_package_value(
    profile: dict[str, Any], bundle: dict[str, Any]
) -> dict[str, Any]:
    """Deterministically reconstruct the package value for verification."""

    action = bundle["action"]
    artifact_id = action["artifact_id"]
    artifact_revision = action["artifact_revision"]
    artifact_sha256 = action["artifact_sha256"]
    release_identity = "\n".join(
        [
            profile["project_id"],
            profile["profile_revision"],
            artifact_id,
            artifact_revision,
            artifact_sha256,
            action["action_id"],
        ]
    )
    release_id = str(uuid.uuid5(RELEASE_NAMESPACE, release_identity))
    return {
        "release_package_version": "1.0",
        "release_id": release_id,
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "artifact_id": artifact_id,
        "artifact_revision": artifact_revision,
        "artifact_sha256": artifact_sha256,
        "source_run_id": bundle["source_run_id"],
        "source_artifact": _pointer(
            bundle["artifact_ref"], artifact_revision, bundle["artifact_bytes"]
        ),
        "origin_artifact": bundle["origin_pointer"],
        "review_manifest": _pointer(
            bundle["review_ref"], artifact_revision, bundle["review_bytes"]
        ),
        "lineage": _pointer(
            bundle["lineage_ref"], artifact_revision, bundle["lineage_bytes"]
        ),
        "human_approval": {
            "action": "approve",
            "action_id": action["action_id"],
            "action_ref": _pointer(
                bundle["action_ref"], action["action_id"], bundle["action_bytes"]
            ),
            "recorded_at": action["recorded_at"],
        },
        "root_writer_receipts": [
            bundle["review_receipt"],
            bundle["approval_receipt"],
        ],
        "adapter_port_version": "1.0",
        "contains_credentials": False,
        "external_side_effects": False,
        "created_at": action["recorded_at"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--project-config", required=True, type=Path)
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--artifact-revision", required=True)
    parser.add_argument("--artifact-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        result = create_release_package(
            args.workspace,
            args.project_config,
            artifact_id=args.artifact_id,
            artifact_revision=args.artifact_revision,
            artifact_sha256=args.artifact_sha256,
        )
    except ReleaseError as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
