"""Durable Postiz publisher task for one exact approved release package."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import uuid

from pipeline import postiz_connector, release_core, root_writer
from pipeline.task_broker import BrokerError, TaskBroker


class PublisherError(BrokerError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def queue_publisher_task(
    broker: TaskBroker,
    *,
    workspace: Path,
    profile_path: Path,
    parent_task_id: str,
    approval_id: str,
    release_package: dict[str, str],
    platform: str,
    operation: str,
    requested_at: str,
    scheduled_at: str | None,
) -> dict:
    """Persist one exact delivery request, then admit one replay-safe task."""
    workspace = root_writer._validate_workspace(Path(workspace))
    profile_path = Path(profile_path)
    profile = root_writer.load_project_profile(profile_path)
    root_writer._validate_profile_package(workspace, profile_path, profile)
    if (not isinstance(approval_id, str)
            or not root_writer.RUN_ID_RE.fullmatch(approval_id)
            or platform not in postiz_connector.PLATFORMS
            or operation not in postiz_connector.OPERATIONS
            or not postiz_connector.RFC3339_UTC_RE.fullmatch(requested_at or "")
            or (operation == "schedule") != (scheduled_at is not None)
            or (scheduled_at is not None
                and not postiz_connector.RFC3339_UTC_RE.fullmatch(scheduled_at))):
        raise PublisherError("Publisher request is invalid")
    if not isinstance(release_package, dict) or set(release_package) != {
        "artifact_ref", "artifact_revision", "artifact_sha256",
    }:
        raise PublisherError("Publisher release package pointer is invalid")
    try:
        release_core._validate_pointer(
            release_package, "publisher release package")
    except release_core.ReleaseError as exc:
        raise PublisherError("Publisher release package pointer is invalid") from exc
    if operation == "schedule" and scheduled_at <= requested_at:
        raise PublisherError("Publisher schedule must be after approval")
    writer_run_id = f"publisher-request-{approval_id.replace('-', '')[:20]}"
    request_ref = f"records/publisher-requests/{approval_id}.json"
    record = {
        "publisher_request_version": "1.0",
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "request_id": approval_id,
        "parent_task_id": parent_task_id,
        "approval_id": approval_id,
        "release_package": release_package,
        "platform": platform,
        "operation": operation,
        "requested_at": requested_at,
        "scheduled_at": scheduled_at,
        "writer_run_id": writer_run_id,
    }
    content = _canonical(record)
    digest = hashlib.sha256(content).hexdigest()
    request = release_core._writer_request(
        profile,
        writer_run_id,
        str(uuid.uuid5(uuid.NAMESPACE_URL,
                       f"growth-publisher-request:{profile['project_id']}:{approval_id}")),
        [release_core._write_item(request_ref, content)],
    )
    release_core._apply_request(workspace, profile_path, request)
    origin_key = hashlib.sha256(_canonical([
        "agent", {"parent_task_id": parent_task_id,
                  "event_id": f"publisher:{approval_id}"},
    ])).hexdigest()
    return broker.admit(
        project=profile["project_id"], origin="agent", origin_key=origin_key,
        agent="publisher", input_ref=request_ref, input_sha256=digest,
        actor="local-os-operator", not_before=0, max_attempts=1,
        parent_task_id=parent_task_id,
    )


def _read_request(
    broker: TaskBroker, *, workspace: Path, profile: dict, task: dict,
) -> dict:
    if task["agent_id"] != "publisher" or task["status"] != "queued":
        raise PublisherError("Publisher task is not ready")
    parent = broker.get(task["project_id"], task["parent_task_id"])
    if parent["agent_id"] != "mavery-qa" or parent["status"] != "completed":
        raise PublisherError("Publisher task lacks one completed QA parent")
    reference = PurePosixPath(task["input_ref"])
    if (reference.is_absolute() or len(reference.parts) != 3
            or reference.parts[:2] != ("records", "publisher-requests")
            or reference.suffix != ".json"):
        raise PublisherError("Publisher input reference is invalid")
    admitted = broker.db.execute(
        """SELECT details_json FROM audit_events
        WHERE project_id=? AND task_id=? AND event_type='admitted'""",
        (task["project_id"], task["task_id"]),
    ).fetchone()
    if admitted is None:
        raise PublisherError("Publisher task lacks admission proof")
    try:
        with root_writer._project_state_fd(workspace, profile["_state_root"]) as state_fd:
            content = root_writer._read_relative_bytes(state_fd, reference)
        if hashlib.sha256(content).hexdigest() != json.loads(admitted[0]).get(
                "input_sha256"):
            raise PublisherError("Publisher input hash changed")
        value = json.loads(content.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, PublisherError):
            raise
        raise PublisherError("Publisher input cannot be verified") from exc
    fields = {
        "publisher_request_version", "project_id", "project_profile_revision",
        "request_id", "parent_task_id", "approval_id", "release_package",
        "platform", "operation", "requested_at", "scheduled_at",
        "writer_run_id",
    }
    package = value.get("release_package") if isinstance(value, dict) else None
    if (not isinstance(value, dict) or set(value) != fields
            or value["publisher_request_version"] != "1.0"
            or value["project_id"] != task["project_id"]
            or value["project_profile_revision"] != profile["profile_revision"]
            or value["parent_task_id"] != task["parent_task_id"]
            or not isinstance(value["request_id"], str)
            or not isinstance(value["approval_id"], str)
            or value["request_id"] != value["approval_id"]
            or reference.name != f"{value['request_id']}.json"
            or not isinstance(value["writer_run_id"], str)
            or not root_writer.RUN_ID_RE.fullmatch(value["writer_run_id"])
            or not isinstance(value["platform"], str)
            or value["platform"] not in postiz_connector.PLATFORMS
            or not isinstance(value["operation"], str)
            or value["operation"] not in postiz_connector.OPERATIONS
            or not isinstance(value["requested_at"], str)
            or not postiz_connector.RFC3339_UTC_RE.fullmatch(value["requested_at"])
            or (value["scheduled_at"] is not None
                and not isinstance(value["scheduled_at"], str))
            or (value["operation"] == "schedule")
                != (value["scheduled_at"] is not None)
            or (value["scheduled_at"] is not None and not
                postiz_connector.RFC3339_UTC_RE.fullmatch(value["scheduled_at"]))
            or not isinstance(package, dict) or set(package) != {
                "artifact_ref", "artifact_revision", "artifact_sha256",
            }):
        raise PublisherError("Publisher request fields are invalid")
    try:
        release_core._validate_pointer(package, "publisher release package")
    except release_core.ReleaseError as exc:
        raise PublisherError("Publisher release package pointer is invalid") from exc
    if (value["operation"] == "schedule"
            and value["scheduled_at"] <= value["requested_at"]):
        raise PublisherError("Publisher schedule must be after approval")
    release_core._receipt_covering(
        workspace, profile, value["writer_run_id"], reference.as_posix(), content)
    return value


def run_publisher_task(
    broker: TaskBroker,
    *,
    project: str,
    task_id: str,
    workspace: Path,
    profile_path: Path,
    executable: Path | None = None,
    timeout: int = 600,
) -> dict:
    """Deliver once, then complete the broker task with the verified receipt."""
    del executable
    workspace = root_writer._validate_workspace(Path(workspace))
    profile_path = Path(profile_path)
    profile = root_writer.load_project_profile(profile_path)
    root_writer._validate_profile_package(workspace, profile_path, profile)
    if profile["project_id"] != project:
        raise PublisherError("Publisher profile does not match task project")
    permissions = broker.permissions("publisher")
    if (permissions["browser"] != "none"
            or permissions["credentials"] != "per-channel-connector-only"):
        raise PublisherError("Publisher permission is invalid")
    task = broker.get(project, task_id)
    request = _read_request(
        broker, workspace=workspace, profile=profile, task=task)
    claim = broker.claim(
        project, task_id, agent="publisher", lease_seconds=timeout)
    try:
        delivered = postiz_connector.deliver(
            workspace, profile_path,
            release_package=request["release_package"],
            platform=request["platform"], operation=request["operation"],
            requested_at=request["requested_at"],
            scheduled_at=request["scheduled_at"],
        )
        receipt = delivered.get("receipt")
        if not isinstance(receipt, dict) or set(receipt) != {
            "artifact_ref", "artifact_revision", "artifact_sha256", "byte_size",
        }:
            raise PublisherError("Publisher delivery lacks a verified receipt")
        content = release_core._state_bytes(
            workspace, profile, receipt["artifact_ref"])
        if (content is None
                or hashlib.sha256(content).hexdigest() != receipt["artifact_sha256"]
                or len(content) != receipt["byte_size"]):
            raise PublisherError("Publisher receipt bytes do not match delivery")
        receipt_run_id = (
            f"publisher-receipt-{delivered['intent_id'].replace('-', '')[:20]}"
        )
        release_core._receipt_covering(
            workspace, profile, receipt_run_id, receipt["artifact_ref"], content)
        completed = broker.accept_verified_completion(
            project, task_id, agent="publisher", token=claim["lease_token"],
            version=claim["version"], asset={
                "asset_id": delivered["intent_id"],
                "revision": receipt["artifact_revision"],
                "sha256": receipt["artifact_sha256"],
                "artifact_ref": receipt["artifact_ref"],
                "media_type": "application/json",
                "byte_size": receipt["byte_size"],
            })
        return {
            "publisher_task_id": task_id,
            "publisher_status": completed["status"],
            "delivery_status": delivered["status"],
            "receipt": receipt,
        }
    except Exception:
        try:
            broker.fail(
                project, task_id, agent="publisher", token=claim["lease_token"],
                version=claim["version"])
        except BrokerError:
            pass
        raise
