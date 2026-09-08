#!/usr/bin/env python3
"""Immutable, project-scoped project-start readiness control plane.

This module turns the first useful project inputs into a small deterministic
read model: goal, audience, success signal, non-goals, and four explicit
readiness steps. It stores only immutable JSON through :mod:`root_writer`.
It does not call a model, schedule work, publish, or hold credentials.

The public ``ProjectStartService`` is deliberately easy for a local API to use:

* ``project_desk(project_id)`` returns customer-safe display data only;
* ``create_start_brief(payload)`` records an operator-authored start brief;
* ``append_readiness_snapshot(payload)`` records an evidence-bound update.

Technical references and hashes stay in canonical state. They are verified before
they influence the customer-safe read model and are never returned by it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any, Iterable
import uuid

from pipeline import context_resolver, root_writer
from pipeline.validate_json_suites import (
    SuiteConfigurationError,
    validate_registered_instance,
)


LEGACY_BRIEF_SCHEMA_ID = "project-start-brief@1"
CURRENT_BRIEF_SCHEMA_ID = "project-start-brief@2"
READINESS_SCHEMA_ID = "project-start-readiness@1"
BRIEF_ROOT = PurePosixPath("records/project-start/briefs")
READINESS_ROOT = PurePosixPath("records/project-start/readiness")
RFC3339_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
START_ID_RE = re.compile(r"^START-[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
SNAPSHOT_ID_RE = re.compile(r"^START-READY-[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
REVISION_RE = re.compile(r"^r[1-9][0-9]{0,5}$")
PUBLISH_MODES = frozenset({"off", "review", "automatic"})
PERMISSIONS_MAX_BYTES = 64 * 1024


def _runtime_publish_authority(workspace: Path) -> dict[str, Any]:
    """Read the one local publish switch without granting publisher capability."""

    path = workspace / "runtime" / "permissions.json"
    if not path.exists():
        mode = "review"
        configuration = "safe_default"
    else:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > PERMISSIONS_MAX_BYTES:
                raise ValueError()
            value = json.loads(
                path.read_text(encoding="utf-8"),
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
            mode = value["publish"]
            if not isinstance(value, dict) or mode not in PUBLISH_MODES:
                raise ValueError()
            configuration = "configured"
        except (OSError, UnicodeError, ValueError, KeyError, TypeError):
            mode = "off"
            configuration = "invalid_fail_closed"
    return {
        "publish_mode": mode,
        "publish_configuration": configuration,
        "automatic_publish": mode == "automatic",
        "human_review_required": mode != "automatic",
        "ready_to_queue_is_not_go_live": True,
    }
SERVICE_NAMESPACE = uuid.UUID("3e82e18c-b7fc-4d66-ae22-2c359b0a229c")


class ProjectStartError(ValueError):
    """A safe API-facing project-start failure.

    ``message`` is intentionally free of source paths, hashes, model output, and
    customer data. A local HTTP adapter can return these three attributes without
    having to expose a traceback.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int = 409,
        code: str = "invalid_project_start",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _canonical_json(value: Any) -> bytes:
    try:
        return (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError) as exc:
        raise ProjectStartError("Project-start values must be UTF-8 JSON") from exc


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


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
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProjectStartError(f"Cannot parse {label} as strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ProjectStartError(f"{label} must be a JSON object")
    return value


def _exact_keys(
    value: Any, required: set[str], optional: set[str], label: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProjectStartError(f"{label} must be an object")
    missing = sorted(required - value.keys())
    extra = sorted(value.keys() - required - optional)
    if missing:
        raise ProjectStartError(f"{label} missing fields: {', '.join(missing)}")
    if extra:
        raise ProjectStartError(f"{label} has unknown fields: {', '.join(extra)}")
    return value


def _nonblank(value: Any, label: str, *, minimum: int = 1) -> str:
    if not isinstance(value, str) or len(value.strip()) < minimum or "\x00" in value:
        raise ProjectStartError(f"{label} must be an explicit non-empty string")
    return value


def _parse_timestamp(value: Any, label: str) -> dt.datetime:
    if not isinstance(value, str) or not RFC3339_UTC_RE.fullmatch(value):
        raise ProjectStartError(f"{label} must be RFC3339 UTC seconds")
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise ProjectStartError(f"{label} is not a real UTC timestamp") from exc


def _now_rfc3339() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _workspace_for_profile(profile_path: Path) -> Path:
    try:
        resolved = profile_path.resolve(strict=True)
    except OSError as exc:
        raise ProjectStartError("Project profile cannot be resolved") from exc
    if resolved.name != "project.json" or resolved.parent.parent.name != "projects":
        raise ProjectStartError(
            "Project profile must live at <workspace>/projects/<profile>/project.json"
        )
    return resolved.parents[2]


def _load_profile(
    workspace_path: Path, profile_path: Path
) -> tuple[Path, Path, dict[str, Any]]:
    try:
        workspace = root_writer._validate_workspace(workspace_path)
        profile = root_writer.load_project_profile(profile_path)
        root_writer._validate_profile_package(workspace, profile_path, profile)
    except root_writer.WriterError as exc:
        raise ProjectStartError(str(exc)) from exc
    return workspace, profile_path.resolve(), profile


def _profile_for_path(profile_path: Path) -> tuple[Path, dict[str, Any]]:
    workspace = _workspace_for_profile(profile_path)
    resolved_workspace, _, profile = _load_profile(workspace, profile_path)
    return resolved_workspace, profile


def _schema_validate(profile_path: Path, schema_id: str, value: dict[str, Any], label: str) -> None:
    try:
        errors = validate_registered_instance(profile_path, schema_id, value)
    except SuiteConfigurationError as exc:
        raise ProjectStartError(f"Cannot validate {label}: {exc}") from exc
    if errors:
        first = errors[0]
        raise ProjectStartError(
            f"{label} violates {schema_id} at {first.instance_path}: {first.message}"
        )


def _brief_schema_id(brief: dict[str, Any]) -> str:
    version = brief.get("brief_version")
    if version == "1.0":
        return LEGACY_BRIEF_SCHEMA_ID
    if version == "2.0":
        return CURRENT_BRIEF_SCHEMA_ID
    raise ProjectStartError("Project start brief version is unsupported")


def _identity_matches(value: dict[str, Any], profile: dict[str, Any], label: str) -> None:
    if value.get("project_id") != profile["project_id"]:
        raise ProjectStartError(f"Cross-project {label} is forbidden")
    if value.get("project_profile_revision") != profile["profile_revision"]:
        raise ProjectStartError(f"{label} uses a stale project profile revision")


def _safe_relative(raw: Any, label: str) -> PurePosixPath:
    try:
        return root_writer._validate_relative_path(raw, label)
    except root_writer.WriterError as exc:
        raise ProjectStartError(str(exc)) from exc


def _under(path: PurePosixPath, prefix: PurePosixPath) -> bool:
    return root_writer._under_prefix(path, prefix)


def _state_path(
    workspace: Path, profile: dict[str, Any], raw_ref: Any, label: str
) -> tuple[PurePosixPath, Path]:
    relative = _safe_relative(raw_ref, label)
    try:
        path = root_writer._assert_no_symlink_path(
            workspace, profile["_state_root"] / relative
        )
    except root_writer.WriterError as exc:
        raise ProjectStartError(str(exc)) from exc
    return relative, path


def _read_pointer(
    workspace: Path,
    profile: dict[str, Any],
    raw_pointer: Any,
    label: str,
    *,
    required_prefix: PurePosixPath,
) -> tuple[dict[str, str], bytes]:
    pointer = _exact_keys(
        raw_pointer,
        {"artifact_ref", "artifact_revision", "artifact_sha256"},
        set(),
        label,
    )
    relative, path = _state_path(workspace, profile, pointer["artifact_ref"], f"{label}.artifact_ref")
    if relative == required_prefix or not _under(relative, required_prefix):
        raise ProjectStartError(f"{label}.artifact_ref must live below {required_prefix}")
    revision = pointer["artifact_revision"]
    digest = pointer["artifact_sha256"]
    if not isinstance(revision, str) or not revision:
        raise ProjectStartError(f"{label}.artifact_revision must be a non-empty string")
    if not isinstance(digest, str) or not root_writer.SHA256_RE.fullmatch(digest):
        raise ProjectStartError(f"{label}.artifact_sha256 is invalid")
    if not path.is_file():
        raise ProjectStartError(f"{label} does not exist")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ProjectStartError(f"Cannot read {label}") from exc
    if _sha256(content) != digest:
        raise ProjectStartError(f"{label} hash mismatch")
    return {
        "artifact_ref": relative.as_posix(),
        "artifact_revision": revision,
        "artifact_sha256": digest,
    }, content


def _evidence_refs(
    workspace: Path, profile: dict[str, Any], raw_refs: Any, label: str
) -> list[dict[str, str]]:
    if not isinstance(raw_refs, list):
        raise ProjectStartError(f"{label} must be an array")
    resolved: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_refs):
        pointer, _ = _read_pointer(
            workspace,
            profile,
            raw,
            f"{label}[{index}]",
            required_prefix=PurePosixPath("evidence"),
        )
        ref = pointer["artifact_ref"]
        if ref in seen:
            raise ProjectStartError(f"{label} contains duplicate artifact_ref values")
        seen.add(ref)
        resolved.append(pointer)
    return resolved


def brief_relative(brief: dict[str, Any]) -> PurePosixPath:
    brief_id = brief.get("brief_id")
    revision = brief.get("brief_revision")
    if not isinstance(brief_id, str) or not START_ID_RE.fullmatch(brief_id):
        raise ProjectStartError("brief_id is invalid")
    if not isinstance(revision, str) or not REVISION_RE.fullmatch(revision):
        raise ProjectStartError("brief_revision is invalid")
    return BRIEF_ROOT / brief_id / f"{revision}.json"


def readiness_relative(snapshot: dict[str, Any], brief: dict[str, Any]) -> PurePosixPath:
    snapshot_id = snapshot.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not SNAPSHOT_ID_RE.fullmatch(snapshot_id):
        raise ProjectStartError("snapshot_id is invalid")
    return READINESS_ROOT / str(brief["brief_id"]) / f"{snapshot_id}.json"


def validate_start_brief(profile_path: Path, brief: dict[str, Any]) -> None:
    """Validate operator-owned project inputs against the selected project."""

    workspace, profile = _profile_for_path(profile_path)
    _schema_validate(
        profile_path, _brief_schema_id(brief), brief, "project start brief"
    )
    _identity_matches(brief, profile, "project start brief")
    if not START_ID_RE.fullmatch(brief["brief_id"]):
        raise ProjectStartError("brief_id is invalid")
    if not REVISION_RE.fullmatch(brief["brief_revision"]):
        raise ProjectStartError("brief_revision is invalid")
    _parse_timestamp(brief["created_at"], "brief.created_at")
    for field in ("goal", "audience", "success_signal"):
        value = brief[field]
        _nonblank(value.get("title", value.get("label")), f"brief.{field}.label", minimum=3)
        _nonblank(value["detail"], f"brief.{field}.detail", minimum=10)
    if not isinstance(brief["non_goals"], list) or not brief["non_goals"]:
        raise ProjectStartError("brief.non_goals must contain at least one explicit item")
    if any(not isinstance(item, str) or len(item.strip()) < 3 for item in brief["non_goals"]):
        raise ProjectStartError("brief.non_goals must contain explicit text")
    _nonblank(brief["research_question"], "brief.research_question", minimum=10)
    if brief["brief_version"] == "2.0":
        baseline = brief["baseline"]
        if baseline["status"] not in {"measured", "not_measured"}:
            raise ProjectStartError("brief.baseline.status is invalid")
        _nonblank(baseline["detail"], "brief.baseline.detail", minimum=10)
        horizon = brief["horizon"]
        _nonblank(horizon["label"], "brief.horizon.label", minimum=3)
        _nonblank(horizon["detail"], "brief.horizon.detail", minimum=10)
        _nonblank(brief["resources"]["detail"], "brief.resources.detail", minimum=10)
        _nonblank(
            brief["do_nothing_option"]["detail"],
            "brief.do_nothing_option.detail",
            minimum=10,
        )
    if brief["authority"] != {
        "may_publish": False,
        "may_change_public_accounts": False,
        "human_review_required": True,
    }:
        raise ProjectStartError("Project start brief cannot grant publishing authority")
    if brief["created_by"] != "operator" or brief["contains_personal_data"] is not False:
        raise ProjectStartError("Project start brief provenance or privacy boundary is invalid")
    # Touch the profile state path through the same safe path boundary used later.
    _state_path(workspace, profile, brief_relative(brief).as_posix(), "brief target")


def _resolved_brief_from_pointer(
    workspace: Path, profile_path: Path, profile: dict[str, Any], raw_pointer: Any
) -> tuple[dict[str, str], dict[str, Any]]:
    pointer, content = _read_pointer(
        workspace,
        profile,
        raw_pointer,
        "readiness brief",
        required_prefix=BRIEF_ROOT,
    )
    if not REVISION_RE.fullmatch(pointer["artifact_revision"]):
        raise ProjectStartError("readiness brief revision is invalid")
    brief = _strict_json(content, "readiness brief")
    validate_start_brief(profile_path, brief)
    if pointer["artifact_revision"] != brief["brief_revision"]:
        raise ProjectStartError("readiness brief pointer revision does not match brief")
    if pointer["artifact_ref"] != brief_relative(brief).as_posix():
        raise ProjectStartError("readiness brief pointer does not match immutable brief path")
    return pointer, brief


def validate_start_readiness(profile_path: Path, snapshot: dict[str, Any]) -> None:
    """Validate one immutable readiness update and every referenced byte."""

    workspace, profile = _profile_for_path(profile_path)
    _schema_validate(profile_path, READINESS_SCHEMA_ID, snapshot, "project start readiness")
    _identity_matches(snapshot, profile, "project start readiness")
    if not SNAPSHOT_ID_RE.fullmatch(snapshot["snapshot_id"]):
        raise ProjectStartError("snapshot_id is invalid")
    _parse_timestamp(snapshot["recorded_at"], "readiness.recorded_at")
    pointer, brief = _resolved_brief_from_pointer(
        workspace, profile_path, profile, snapshot["brief"]
    )
    if snapshot["brief"] != pointer:
        raise ProjectStartError("readiness brief pointer is not canonical")
    for field in ("audience", "evidence", "first_recommendation"):
        value = snapshot[field]
        _nonblank(value["detail"], f"readiness.{field}.detail", minimum=10)
        _evidence_refs(workspace, profile, value["evidence_refs"], f"readiness.{field}.evidence_refs")
    if snapshot["first_recommendation"]["status"] == "ready":
        if snapshot["audience"]["status"] != "validated":
            raise ProjectStartError("A ready recommendation requires a validated audience")
        if snapshot["evidence"]["status"] != "sufficient":
            raise ProjectStartError("A ready recommendation requires sufficient evidence")
    allowed_roles = set(profile.get("role_ids", [])) | {profile["root_role_id"]}
    if snapshot["recorded_by"] not in allowed_roles:
        raise ProjectStartError("readiness.recorded_by is not enabled by this project profile")
    if snapshot["authority"] != {
        "may_publish": False,
        "may_change_public_accounts": False,
        "human_review_required": True,
    }:
        raise ProjectStartError("Project-start readiness cannot grant publishing authority")
    if snapshot["contains_personal_data"] is not False:
        raise ProjectStartError("Project-start readiness must not store personal data")
    expected = readiness_relative(snapshot, brief)
    _state_path(workspace, profile, expected.as_posix(), "readiness target")


def build_start_brief_write_request(
    profile_path: Path,
    brief: dict[str, Any],
    *,
    run_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    validate_start_brief(profile_path, brief)
    _, profile = _profile_for_path(profile_path)
    content = _canonical_json(brief)
    return {
        "writer_request_version": root_writer.REQUEST_VERSION,
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "run_id": run_id,
        "idempotency_key": idempotency_key,
        "requested_by": profile["root_role_id"],
        "writes": [
            {
                "path": brief_relative(brief).as_posix(),
                "mode": "create",
                "content": content.decode("utf-8"),
                "content_sha256": _sha256(content),
                "expected_sha256": None,
                "media_type": "application/json",
            }
        ],
    }


def append_start_brief(
    workspace_path: Path,
    profile_path: Path,
    brief: dict[str, Any],
    *,
    run_id: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Append one exact operator brief through the Root Writer."""

    key = idempotency_key or str(uuid.uuid4())
    request = build_start_brief_write_request(
        profile_path, brief, run_id=run_id, idempotency_key=key
    )
    with tempfile.TemporaryDirectory(prefix="growth-project-start-") as temporary:
        request_path = Path(temporary) / "request.json"
        request_path.write_bytes(_canonical_json(request))
        try:
            return root_writer.apply_request(workspace_path, profile_path, request_path)
        except root_writer.WriterError as exc:
            raise ProjectStartError(str(exc)) from exc


def build_readiness_write_request(
    profile_path: Path,
    snapshot: dict[str, Any],
    *,
    run_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    validate_start_readiness(profile_path, snapshot)
    workspace, profile = _profile_for_path(profile_path)
    _, brief = _resolved_brief_from_pointer(
        workspace, profile_path, profile, snapshot["brief"]
    )
    content = _canonical_json(snapshot)
    return {
        "writer_request_version": root_writer.REQUEST_VERSION,
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "run_id": run_id,
        "idempotency_key": idempotency_key,
        "requested_by": profile["root_role_id"],
        "writes": [
            {
                "path": readiness_relative(snapshot, brief).as_posix(),
                "mode": "create",
                "content": content.decode("utf-8"),
                "content_sha256": _sha256(content),
                "expected_sha256": None,
                "media_type": "application/json",
            }
        ],
    }


def append_readiness_snapshot(
    workspace_path: Path,
    profile_path: Path,
    snapshot: dict[str, Any],
    *,
    run_id: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Append one exact evidence-bound readiness snapshot through the Root Writer."""

    key = idempotency_key or str(uuid.uuid4())
    request = build_readiness_write_request(
        profile_path, snapshot, run_id=run_id, idempotency_key=key
    )
    with tempfile.TemporaryDirectory(prefix="growth-project-readiness-") as temporary:
        request_path = Path(temporary) / "request.json"
        request_path.write_bytes(_canonical_json(request))
        try:
            return root_writer.apply_request(workspace_path, profile_path, request_path)
        except root_writer.WriterError as exc:
            raise ProjectStartError(str(exc)) from exc


def _state_json_paths(
    workspace: Path, profile: dict[str, Any], root: PurePosixPath
) -> list[tuple[PurePosixPath, Path]]:
    """Return safe JSON paths from one fixed project-state subtree."""

    _, state_root = _state_path(workspace, profile, root.as_posix(), "state subtree")
    if not state_root.exists():
        return []
    if state_root.is_symlink() or not state_root.is_dir():
        raise ProjectStartError(f"Project-start state subtree is unsafe: {root}")
    paths: list[tuple[PurePosixPath, Path]] = []
    try:
        candidates = sorted(state_root.rglob("*.json"), key=lambda item: item.as_posix())
    except OSError as exc:
        raise ProjectStartError("Cannot inspect project-start state") from exc
    profile_state = workspace.joinpath(*profile["_state_root"].parts)
    for candidate in candidates:
        try:
            relative = PurePosixPath(candidate.relative_to(profile_state).as_posix())
        except ValueError as exc:
            raise ProjectStartError("Project-start state path escaped its profile") from exc
        _, safe = _state_path(workspace, profile, relative.as_posix(), "state artifact")
        if safe != candidate or safe.is_symlink() or not safe.is_file():
            raise ProjectStartError("Project-start state contains an unsafe artifact")
        paths.append((relative, safe))
    return paths


def _load_briefs(profile_path: Path) -> list[tuple[dict[str, str], dict[str, Any]]]:
    workspace, profile = _profile_for_path(profile_path)
    loaded: list[tuple[dict[str, str], dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    for relative, path in _state_json_paths(workspace, profile, BRIEF_ROOT):
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ProjectStartError("Cannot read stored project start brief") from exc
        brief = _strict_json(content, "stored project start brief")
        validate_start_brief(profile_path, brief)
        if relative != brief_relative(brief):
            raise ProjectStartError("Stored project start brief path does not match its identity")
        identity = (brief["brief_id"], brief["brief_revision"])
        if identity in seen:
            raise ProjectStartError("Duplicate project start brief identity")
        seen.add(identity)
        loaded.append(
            (
                {
                    "artifact_ref": relative.as_posix(),
                    "artifact_revision": brief["brief_revision"],
                    "artifact_sha256": _sha256(content),
                },
                brief,
            )
        )
    return loaded


def _choose_brief(
    briefs: Iterable[tuple[dict[str, str], dict[str, Any]]], brief_id: str | None
) -> tuple[dict[str, str], dict[str, Any]] | None:
    matching = [item for item in briefs if brief_id is None or item[1]["brief_id"] == brief_id]
    if not matching:
        return None
    return max(
        matching,
        key=lambda item: (
            _parse_timestamp(item[1]["created_at"], "brief.created_at"),
            item[1]["brief_id"],
            item[1]["brief_revision"],
        ),
    )


def _choose_direction(
    briefs: Iterable[tuple[dict[str, str], dict[str, Any]]],
) -> tuple[dict[str, str], dict[str, Any]] | None:
    """Return the first immutable operator brief as the project direction.

    Later briefs are bounded work orders. They must not silently replace the
    longer-lived reason the project exists merely because they were created
    more recently.
    """

    values = list(briefs)
    if not values:
        return None
    return min(
        values,
        key=lambda item: (
            _parse_timestamp(item[1]["created_at"], "brief.created_at"),
            item[1]["brief_id"],
            item[1]["brief_revision"],
        ),
    )


def _load_readiness_snapshots(
    profile_path: Path,
    brief_pointer: dict[str, str],
) -> list[dict[str, Any]]:
    workspace, profile = _profile_for_path(profile_path)
    loaded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for relative, path in _state_json_paths(workspace, profile, READINESS_ROOT):
        try:
            value = _strict_json(path.read_bytes(), "stored project-start readiness")
        except OSError as exc:
            raise ProjectStartError("Cannot read stored project-start readiness") from exc
        if value.get("project_id") != profile["project_id"]:
            raise ProjectStartError("Cross-project project-start readiness is forbidden")
        # Historical snapshots remain immutable after a profile revision changes;
        # only a current-revision snapshot can affect the selected project desk.
        if value.get("project_profile_revision") != profile["profile_revision"]:
            continue
        validate_start_readiness(profile_path, value)
        _, brief = _resolved_brief_from_pointer(workspace, profile_path, profile, value["brief"])
        if relative != readiness_relative(value, brief):
            raise ProjectStartError("Stored readiness path does not match its identity")
        if value["snapshot_id"] in seen:
            raise ProjectStartError("Duplicate project-start readiness snapshot")
        seen.add(value["snapshot_id"])
        if value["brief"] == brief_pointer:
            loaded.append(value)
    return loaded


def _latest_snapshot(snapshots: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    values = list(snapshots)
    if not values:
        return None
    return max(
        values,
        key=lambda value: (
            _parse_timestamp(value["recorded_at"], "readiness.recorded_at"),
            value["snapshot_id"],
        ),
    )


def _context_step(profile_path: Path, profile: dict[str, Any]) -> dict[str, Any]:
    pointer = profile.get("project_context")
    if not isinstance(pointer, dict):
        return {
            "id": "context",
            "label": "Project context",
            "status": "blocked",
            "detail": "A verified project context pack is still needed before research can begin.",
            "owner_role": "evidence-research",
        }
    synthetic = profile.get("product_motion") == "synthetic-fixture"
    if synthetic:
        try:
            resolved = context_resolver.resolve_project_context(
                profile_path, pointer, run_mode="fixture"
            )
        except context_resolver.ContextResolutionError:
            return {
                "id": "context",
                "label": "Project context",
                "status": "blocked",
                "detail": "The synthetic context pack is not ready for fixture work.",
                "owner_role": "evidence-research",
            }
        detail = "A synthetic context pack is ready for fixture work; live assumptions still need real inputs."
    else:
        try:
            context_resolver.resolve_project_context(
                profile_path, pointer, run_mode="live"
            )
            detail = "A verified context pack is ready for live project work."
        except context_resolver.ContextResolutionError:
            try:
                context_resolver.resolve_project_context(
                    profile_path,
                    pointer,
                    run_mode="discovery",
                    required_kinds=context_resolver.DISCOVERY_CONTEXT_KINDS,
                )
            except context_resolver.ContextResolutionError:
                return {
                    "id": "context",
                    "label": "Project context",
                    "status": "blocked",
                    "detail": "The context pack is not safe for autonomous discovery. Repair its authority and prohibited-claim boundaries first.",
                    "owner_role": "evidence-research",
                }
            detail = "Safe discovery context is ready. Missing market, funnel, audience, or metric evidence remains unknown and cannot be used as truth."
    return {
        "id": "context",
        "label": "Project context",
        "status": "complete",
        "detail": detail,
        "owner_role": "evidence-research",
    }


def _step(
    identifier: str, label: str, status: str, detail: str, owner_role: str
) -> dict[str, str]:
    return {
        "id": identifier,
        "label": label,
        "status": status,
        "detail": detail,
        "owner_role": owner_role,
    }


def _empty_desk(profile: dict[str, Any]) -> dict[str, Any]:
    steps = [
        _step(
            "baseline",
            "Baseline needed",
            "waiting",
            "Record a measured baseline or explicitly state that it is not measured yet.",
            "analytics-experimentation",
        ),
        _step(
            "context",
            "Project context",
            "waiting",
            "Context is checked after an operator sets the project brief.",
            "evidence-research",
        ),
        _step(
            "audience",
            "Audience research",
            "waiting",
            "Audience research starts after the project brief is set.",
            "audience-voc",
        ),
        _step(
            "evidence",
            "Evidence",
            "waiting",
            "Evidence collection starts after a defined audience.",
            "evidence-research",
        ),
        _step(
            "first_recommendation",
            "First recommendation",
            "waiting",
            "A recommendation is prepared only after the inputs above are ready.",
            "growth-coordinator",
        ),
    ]
    return {
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "display_name": profile.get("display_name", profile["project_id"]),
        "phase": "brief_required",
        "goal": {
            "title": "Set a project goal",
            "detail": "Name the outcome this project should create before any work is queued.",
        },
        "current_work": {
            "title": "Set the first work item",
            "detail": "The first project brief becomes both the durable direction and the current work item.",
        },
        "audience": {
            "label": "Define the audience",
            "detail": "State who the work should help and which problem matters to them.",
        },
        "success_signal": {
            "label": "Choose a success signal",
            "detail": "Describe the measurable signal or decision threshold that would make the work useful.",
        },
        "baseline": {
            "status": "not_measured",
            "detail": "Record whether a baseline is measured; do not infer one from a project brief.",
        },
        "horizon": {
            "label": "Set a time horizon",
            "detail": "State the decision window before work is queued.",
        },
        "resources": {
            "detail": "State the available people, time, budget, and approved tools.",
        },
        "do_nothing_option": {
            "detail": "State what happens if the project does not proceed and why that remains a valid option.",
        },
        "non_goals": [],
        "readiness": {"completed": 0, "total": 5, "steps": steps},
        "blockers": [],
        "next_roles": [
            {
                "role": "operator",
                "label": "Project owner",
                "focus": "Create the first project brief with goal, audience, success signal, baseline, horizon, resources, non-goals, and a do-nothing option.",
                "status": "active",
            },
            {
                "role": "growth-coordinator",
                "label": "Growth coordinator",
                "focus": "Routes work only after the project brief is complete.",
                "status": "waiting",
            },
        ],
        "active_research": {
            "title": "Start a project",
            "stage": "brief_required",
            "progress_label": "0 of 5 readiness steps complete",
            "detail": "No research has started. The first step is an operator-authored project brief.",
            "next_step": "Set the goal, audience, success signal, baseline, horizon, resources, non-goals, do-nothing option, and one research question.",
        },
        "authority": {
            "publish_mode": "review",
            "publish_configuration": "safe_default",
            "automatic_publish": False,
            "human_review_required": True,
            "ready_to_queue_is_not_go_live": True,
        },
    }


def _baseline_step(brief: dict[str, Any]) -> dict[str, str]:
    if brief["brief_version"] != "2.0":
        return _step(
            "baseline",
            "Baseline needed",
            "blocked",
            "This existing project brief does not record a baseline. Analytics must add one before impact or experiment claims are made.",
            "analytics-experimentation",
        )
    baseline = brief["baseline"]
    if baseline["status"] == "measured":
        return _step(
            "baseline",
            "Baseline",
            "complete",
            baseline["detail"],
            "analytics-experimentation",
        )
    return _step(
        "baseline",
        "Baseline needed",
        "blocked",
        baseline["detail"],
        "analytics-experimentation",
    )


def _readiness_steps(
    profile_path: Path,
    profile: dict[str, Any],
    brief: dict[str, Any],
    snapshot: dict[str, Any] | None,
) -> list[dict[str, str]]:
    baseline = _baseline_step(brief)
    context = _context_step(profile_path, profile)
    if context["status"] != "complete":
        return [
            baseline,
            context,
            _step("audience", "Audience research", "waiting", "Audience work waits for a verified project context.", "audience-voc"),
            _step("evidence", "Evidence", "waiting", "Evidence work waits for verified project context and an audience focus.", "evidence-research"),
            _step("first_recommendation", "First recommendation", "waiting", "A recommendation waits for context, audience, and evidence.", "growth-coordinator"),
        ]
    if snapshot is None:
        return [
            baseline,
            context,
            _step("audience", "Audience research", "active", "Turn the audience brief into an evidence-backed audience understanding.", "audience-voc"),
            _step("evidence", "Evidence", "waiting", "Evidence synthesis follows audience validation.", "evidence-research"),
            _step("first_recommendation", "First recommendation", "waiting", "A recommendation waits for audience and evidence readiness.", "growth-coordinator"),
        ]

    audience = snapshot["audience"]
    evidence = snapshot["evidence"]
    recommendation = snapshot["first_recommendation"]
    if audience["status"] == "validated":
        audience_step = _step("audience", "Audience research", "complete", audience["detail"], "audience-voc")
    elif audience["status"] == "blocked":
        audience_step = _step("audience", "Audience research", "blocked", audience["detail"], "audience-voc")
    else:
        audience_step = _step("audience", "Audience research", "active", audience["detail"], "audience-voc")

    if audience_step["status"] != "complete":
        return [
            baseline,
            context,
            audience_step,
            _step("evidence", "Evidence", "waiting", "Evidence synthesis waits for an evidence-backed audience understanding.", "evidence-research"),
            _step("first_recommendation", "First recommendation", "waiting", "A recommendation waits for audience and evidence readiness.", "growth-coordinator"),
        ]

    if evidence["status"] == "sufficient":
        evidence_step = _step("evidence", "Evidence", "complete", evidence["detail"], "evidence-research")
    elif evidence["status"] == "insufficient":
        evidence_step = _step("evidence", "Evidence", "blocked", evidence["detail"], "evidence-research")
    else:
        evidence_step = _step("evidence", "Evidence", "active", evidence["detail"], "evidence-research")

    if evidence_step["status"] != "complete":
        return [
            baseline,
            context,
            audience_step,
            evidence_step,
            _step("first_recommendation", "First recommendation", "waiting", "A recommendation waits for sufficient evidence.", "growth-coordinator"),
        ]

    if recommendation["status"] == "ready":
        recommendation_step = _step("first_recommendation", "First recommendation", "complete", recommendation["detail"], "growth-coordinator")
    elif recommendation["status"] == "blocked":
        recommendation_step = _step("first_recommendation", "First recommendation", "blocked", recommendation["detail"], "growth-coordinator")
    else:
        recommendation_step = _step("first_recommendation", "First recommendation", "active", recommendation["detail"], "growth-coordinator")
    return [baseline, context, audience_step, evidence_step, recommendation_step]


def _phase_for(steps: list[dict[str, str]]) -> str:
    by_id = {step["id"]: step for step in steps}
    if by_id["context"]["status"] == "blocked":
        return "context_blocked"
    if by_id["audience"]["status"] != "complete":
        return "audience_research"
    if by_id["evidence"]["status"] != "complete":
        return "evidence_research"
    if by_id["first_recommendation"]["status"] != "complete":
        return "recommendation_draft"
    if by_id["baseline"]["status"] != "complete":
        return "research_recommendation_ready"
    return "ready_to_queue"


def _role_status(steps: list[dict[str, str]], role: str, phase: str) -> str:
    owned = [step for step in steps if step["owner_role"] == role]
    if any(step["status"] in {"active", "blocked"} for step in owned):
        return "active"
    if owned and all(step["status"] == "complete" for step in owned):
        return "complete"
    return "waiting"


def _role_cards(steps: list[dict[str, str]], phase: str) -> list[dict[str, str]]:
    catalog = (
        (
            "analytics-experimentation",
            "Analytics lead",
            "Establish the stated baseline before the project claims readiness.",
        ),
        (
            "evidence-research",
            "Evidence researcher",
            "Verify the context pack and synthesize reliable research evidence.",
        ),
        (
            "audience-voc",
            "Audience researcher",
            "Validate who the project should help, their language, and their current problem.",
        ),
        (
            "growth-coordinator",
            "Growth coordinator",
            "Turn verified inputs into one bounded first recommendation and route.",
        ),
    )
    return [
        {
            "role": role,
            "label": label,
            "focus": focus,
            "status": _role_status(steps, role, phase),
        }
        for role, label, focus in catalog
    ]


def _desk_baseline(brief: dict[str, Any]) -> dict[str, str]:
    if brief["brief_version"] == "2.0":
        return dict(brief["baseline"])
    return {
        "status": "not_measured",
        "detail": "This existing project brief does not record a baseline. Do not infer one from its goal or success signal.",
    }


def _desk_horizon(brief: dict[str, Any]) -> dict[str, str]:
    if brief["brief_version"] == "2.0":
        return dict(brief["horizon"])
    return {
        "label": "Not recorded",
        "detail": "This project brief has no horizon. Create a new project brief to set a decision window.",
    }


def _desk_resources(brief: dict[str, Any]) -> dict[str, str]:
    if brief["brief_version"] == "2.0":
        return dict(brief["resources"])
    return {
        "detail": "This project brief has no resource statement. Create a new project brief to record available resources.",
    }


def _desk_do_nothing_option(brief: dict[str, Any]) -> dict[str, str]:
    if brief["brief_version"] == "2.0":
        return dict(brief["do_nothing_option"])
    return {
        "detail": "This project brief has no do-nothing option. Create a new project brief to make that alternative explicit.",
    }


def build_project_start_read_model(
    workspace_path: Path,
    profile_path: Path,
    *,
    brief_id: str | None = None,
) -> dict[str, Any]:
    """Return a safe, deterministic Project Desk model for one profile.

    The returned dict is intentionally presentation-ready and has no artifact
    references, hashes, source paths, or raw worker output. It only becomes
    ``ready_to_queue``; it never represents publication readiness or go-live.
    """

    workspace, resolved_profile_path, profile = _load_profile(workspace_path, profile_path)
    if brief_id is not None and (not isinstance(brief_id, str) or not START_ID_RE.fullmatch(brief_id)):
        raise ProjectStartError("brief_id is invalid")
    with root_writer._workspace_lock(workspace):
        briefs = _load_briefs(resolved_profile_path)
        chosen = _choose_brief(briefs, brief_id)
        if chosen is None:
            return _empty_desk(profile)
        direction = _choose_direction(briefs)
        if direction is None:  # Defensive: chosen came from the same collection.
            raise ProjectStartError("Project direction is unavailable")
        brief_pointer, brief = chosen
        snapshot = _latest_snapshot(
            _load_readiness_snapshots(resolved_profile_path, brief_pointer)
        )
        steps = _readiness_steps(resolved_profile_path, profile, brief, snapshot)

    completed = sum(step["status"] == "complete" for step in steps)
    phase = _phase_for(steps)
    blockers = [
        {
            "title": step["label"],
            "detail": step["detail"],
            "owner_role": step["owner_role"],
        }
        for step in steps
        if step["status"] == "blocked"
    ]
    current = next((step for step in steps if step["status"] == "active"), None)
    if current is None:
        current_id = {
            "context_blocked": "context",
            "audience_research": "audience",
            "evidence_research": "evidence",
            "recommendation_draft": "first_recommendation",
            "research_recommendation_ready": "baseline",
        }.get(phase)
        current = next(
            (step for step in steps if step["id"] == current_id), None
        )
    if current is None:
        current = next((step for step in steps if step["status"] == "blocked"), steps[-1])
    if phase == "ready_to_queue":
        detail = "The first recommendation is ready to queue for the normal route-selection process. It is not approved for publication."
        next_step = "Choose or revise the proposed route, then prepare a reviewable final product."
    elif phase == "research_recommendation_ready":
        detail = "The bounded research recommendation is ready, but no impact, experiment, or launch claim is ready until Analytics establishes the stated baseline."
        next_step = "Establish the baseline in parallel, then reassess the recommendation for a measurable route."
    else:
        detail = current["detail"]
        next_step = f"{current['label']}: {current['detail']}"
    return {
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "display_name": profile.get("display_name", profile["project_id"]),
        "phase": phase,
        "goal": dict(direction[1]["goal"]),
        "current_work": dict(brief["goal"]),
        "audience": dict(brief["audience"]),
        "success_signal": dict(brief["success_signal"]),
        "baseline": _desk_baseline(brief),
        "horizon": _desk_horizon(brief),
        "resources": _desk_resources(brief),
        "do_nothing_option": _desk_do_nothing_option(brief),
        "non_goals": list(brief["non_goals"]),
        "readiness": {"completed": completed, "total": len(steps), "steps": steps},
        "blockers": blockers,
        "next_roles": _role_cards(steps, phase),
        "active_research": {
            "title": brief["research_question"],
            "stage": phase,
            "progress_label": f"{completed} of {len(steps)} readiness steps complete",
            "detail": detail,
            "next_step": next_step,
        },
        "authority": {
            "publish_mode": "review",
            "publish_configuration": "safe_default",
            "automatic_publish": False,
            "human_review_required": True,
            "ready_to_queue_is_not_go_live": True,
        },
    }


class ProjectStartService:
    """Project selector and append-only service for a future local Project Desk API."""

    def __init__(self, workspace: Path) -> None:
        try:
            self.workspace = root_writer._validate_workspace(workspace)
        except root_writer.WriterError as exc:
            raise ProjectStartError(str(exc)) from exc

    def _profiles(self) -> dict[str, tuple[Path, dict[str, Any]]]:
        projects_root = self.workspace / "projects"
        if not projects_root.is_dir() or projects_root.is_symlink():
            raise ProjectStartError("Workspace projects directory is unavailable")
        profiles: dict[str, tuple[Path, dict[str, Any]]] = {}
        try:
            packages = sorted(projects_root.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise ProjectStartError("Cannot inspect workspace projects") from exc
        for package in packages:
            if package.is_symlink() or not package.is_dir():
                continue
            candidate = package / "project.json"
            if candidate.is_symlink() or not candidate.is_file():
                continue
            try:
                profile = root_writer.load_project_profile(candidate)
                root_writer._validate_profile_package(self.workspace, candidate, profile)
            except root_writer.WriterError as exc:
                raise ProjectStartError(str(exc)) from exc
            project_id = profile["project_id"]
            if project_id in profiles:
                raise ProjectStartError(f"Duplicate project_id: {project_id}")
            profiles[project_id] = (candidate, profile)
        return profiles

    def _selected_profile(self, project_id: Any) -> tuple[Path, dict[str, Any]]:
        if not isinstance(project_id, str) or not root_writer.RUN_ID_RE.fullmatch(project_id):
            raise ProjectStartError("project_id is invalid", status=400, code="invalid_project_id")
        selected = self._profiles().get(project_id)
        if selected is None:
            raise ProjectStartError(
                "Selected project does not exist", status=404, code="project_not_found"
            )
        return selected

    def project_desk(self, project_id: Any) -> dict[str, Any]:
        profile_path, _ = self._selected_profile(project_id)
        return self._project_desk(profile_path)

    def _project_desk(self, profile_path: Path, *, brief_id: str | None = None) -> dict[str, Any]:
        desk = build_project_start_read_model(self.workspace, profile_path, brief_id=brief_id)
        desk["authority"] = _runtime_publish_authority(self.workspace)
        return desk

    def create_start_brief(self, payload: Any) -> dict[str, Any]:
        """Create a brief from customer-safe operator inputs, never from model output."""

        try:
            value = _exact_keys(
                payload,
                {
                    "project_id",
                    "project_profile_revision",
                    "goal",
                    "audience",
                    "success_signal",
                    "baseline",
                    "horizon",
                    "resources",
                    "do_nothing_option",
                    "non_goals",
                    "research_question",
                },
                set(),
                "project-start brief input",
            )
        except ProjectStartError as exc:
            raise ProjectStartError(
                "Complete the required project brief fields and try again.",
                status=400,
                code="invalid_project_brief",
            ) from exc
        profile_path, profile = self._selected_profile(value["project_id"])
        if not isinstance(value["project_profile_revision"], str) or not value[
            "project_profile_revision"
        ].strip():
            raise ProjectStartError(
                "Complete the required project brief fields and try again.",
                status=400,
                code="invalid_project_brief",
            )
        if value["project_profile_revision"] != profile["profile_revision"]:
            raise ProjectStartError(
                "This project changed. Refresh the desk before creating a brief.",
                status=409,
                code="stale_project_profile",
            )
        operator_input = {
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
            "goal": value["goal"],
            "audience": value["audience"],
            "success_signal": value["success_signal"],
            "baseline": value["baseline"],
            "horizon": value["horizon"],
            "resources": value["resources"],
            "do_nothing_option": value["do_nothing_option"],
            "non_goals": value["non_goals"],
            "research_question": value["research_question"],
        }
        stable_input_hash = _sha256(_canonical_json(operator_input))
        brief_id = f"START-{stable_input_hash[:24]}"
        with root_writer._workspace_lock(self.workspace):
            existing = _choose_brief(_load_briefs(profile_path), brief_id)
        if existing is not None:
            return {
                "project_id": profile["project_id"],
                "project_profile_revision": profile["profile_revision"],
                "brief_id": brief_id,
                "brief_revision": existing[1]["brief_revision"],
                "created": False,
                "desk": self._project_desk(profile_path, brief_id=brief_id),
            }
        brief = {
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
            "brief_version": "2.0",
            "brief_id": brief_id,
            "brief_revision": "r1",
            "goal": value["goal"],
            "audience": value["audience"],
            "success_signal": value["success_signal"],
            "baseline": value["baseline"],
            "horizon": value["horizon"],
            "resources": value["resources"],
            "do_nothing_option": value["do_nothing_option"],
            "non_goals": value["non_goals"],
            "research_question": value["research_question"],
            "authority": {
                "may_publish": False,
                "may_change_public_accounts": False,
                "human_review_required": True,
            },
            "created_by": "operator",
            "contains_personal_data": False,
            "created_at": _now_rfc3339(),
        }
        try:
            validate_start_brief(profile_path, brief)
        except ProjectStartError as exc:
            raise ProjectStartError(
                "Check the goal, audience, success signal, baseline, horizon, resources, non-goals, do-nothing option, and research question.",
                status=400,
                code="invalid_project_brief",
            ) from exc
        content_hash = _sha256(_canonical_json(brief))
        run_id = f"START-{brief['brief_id']}-{brief['brief_revision']}"
        if len(run_id) > 128:
            run_id = f"START-{content_hash[:24]}"
        key = str(uuid.uuid5(SERVICE_NAMESPACE, f"brief\n{content_hash}"))
        receipt = append_start_brief(
            self.workspace,
            profile_path,
            brief,
            run_id=run_id,
            idempotency_key=key,
        )
        return {
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
            "brief_id": brief["brief_id"],
            "brief_revision": brief["brief_revision"],
            "created": receipt["status"] == "completed",
            "desk": self._project_desk(profile_path, brief_id=brief["brief_id"]),
        }

    def append_readiness_snapshot(self, payload: Any) -> dict[str, Any]:
        """Append a readiness update; this records state and never dispatches work."""

        value = _exact_keys(
            payload,
            {
                "project_id",
                "snapshot_id",
                "brief",
                "audience",
                "evidence",
                "first_recommendation",
                "recorded_by",
                "recorded_at",
            },
            set(),
            "project-start readiness input",
        )
        profile_path, profile = self._selected_profile(value["project_id"])
        snapshot = {
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
            "readiness_version": "1.0",
            "snapshot_id": value["snapshot_id"],
            "brief": value["brief"],
            "audience": value["audience"],
            "evidence": value["evidence"],
            "first_recommendation": value["first_recommendation"],
            "authority": {
                "may_publish": False,
                "may_change_public_accounts": False,
                "human_review_required": True,
            },
            "recorded_by": value["recorded_by"],
            "contains_personal_data": False,
            "recorded_at": value["recorded_at"],
        }
        content_hash = _sha256(_canonical_json(snapshot))
        run_id = f"START-{snapshot['snapshot_id']}"
        if len(run_id) > 128:
            run_id = f"START-{content_hash[:24]}"
        key = str(uuid.uuid5(SERVICE_NAMESPACE, f"readiness\n{content_hash}"))
        receipt = append_readiness_snapshot(
            self.workspace,
            profile_path,
            snapshot,
            run_id=run_id,
            idempotency_key=key,
        )
        return {
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
            "snapshot_id": snapshot["snapshot_id"],
            "writer_receipt": receipt,
        }
