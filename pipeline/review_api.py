#!/usr/bin/env python3
"""Local, project-neutral review and approval API.

The API reads immutable review manifests and their referenced artifact bytes,
then records one terminal human action for an exact artifact revision through
:mod:`pipeline.root_writer`. An approval creates a verified release package. When
both local publisher switches are explicitly enabled, it queues a durable publisher
task; the HTTP request never calls Postiz. The worker rechecks the connector's
narrower mode and credentials. The API never edits or deletes the reviewed artifact
and has no shell capability.

Pending manifest contract (all fields required, no additional fields)::

    {
      "review_item_version": "1.0",
      "project_id": "example-project",
      "project_profile_revision": "example-profile-v1",
      "artifact_id": "launch-brief",
      "artifact_revision": "r1",
      "artifact_path": "outbox/artifacts/launch-brief/r1.md",
      "artifact_sha256": "<64 lowercase hex>",
      "title": "Launch brief",
      "type": "content-package",
      "preview": "A short human-readable preview.",
      "evidence": [{"label": "Source", "ref": "evidence/packet.json"}],
      "quality_checks": [
        {"name": "claims", "status": "pass", "detail": "3/3 cited"}
      ]
    }

This boundary is intentionally isolated in ``validate_pending_manifest_boundary``
so a future project schema hook can replace the manual validation without
changing the HTTP or storage contract.

Action records contain ``actor_type: human`` and a first-write RFC3339 UTC
``recorded_at`` value. V1 intentionally has no actor identity or authentication:
loopback access under the signed-in operating-system user is its authority
boundary. Deploying this API beyond that boundary requires real authentication.

V1 reviews the verified artifact bytes themselves. Text artifacts are decoded as
strict UTF-8 and returned as ``artifact_content``. A binary artifact fails closed
with ``render_adapter_required`` until an exact-byte render adapter exists; the
manifest preview is context only and is never treated as the reviewed product.

Evidence references are confined to the selected project's ``evidence/`` state
tree. Review responses pin each reference to the SHA-256 of its exact bytes. The
viewer endpoint requires that hash again and rejects a changed, cross-project,
traversing, absolute, or symlinked reference before returning strict UTF-8 text.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tempfile
from typing import Any
from urllib.parse import parse_qs, unquote_to_bytes, urlsplit
import uuid

from pipeline import (
    ga4_connector,
    macos_autostart,
    remote_access,
    root_writer,
    studio,
    website_analytics,
)
from pipeline.goal_loop import GoalLoopError, GoalLoopService
from pipeline.feed_intake import read_current as read_feed_intake
from pipeline.research_synthesis import read_saved as read_synthesis
from pipeline.project_start import ProjectStartError, ProjectStartService
from pipeline.root_writer import WriterError
from pipeline.task_broker import BrokerError


REVIEW_ITEM_VERSION = "1.0"
REVIEW_ACTION_VERSION = "1.0"
MAX_REQUEST_BYTES = 64 * 1024
MAX_TITLE_CHARS = 240
MAX_TYPE_CHARS = 120
MAX_PREVIEW_CHARS = 50_000
MAX_NOTE_CHARS = 20_000
MAX_STATIC_BYTES = 4 * 1024 * 1024
ACTION_NAMESPACE = uuid.UUID("f09a664a-d8a1-4ce8-9bea-5186bf55e282")
PORTABLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
QUALITY_STATUSES = {"pass", "warn", "fail", "not_run"}
REVIEW_EVIDENCE_ROOTS = (
    PurePosixPath("evidence"),
    PurePosixPath("records/governance"),
)
RFC3339_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
ACTION_TO_STATUS = {
    "approve": "approved",
    "decline": "declined",
    "note": "rework_requested",
}


class ReviewError(ValueError):
    """Expected review boundary failure with an HTTP-safe status and code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError) as exc:
        raise ReviewError(400, "invalid_json", "Body must be canonical UTF-8 JSON") from exc


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _exact_keys(
    value: dict[str, Any], required: set[str], optional: set[str], label: str
) -> None:
    missing = sorted(required - value.keys())
    extra = sorted(value.keys() - required - optional)
    if missing:
        raise ReviewError(400, "invalid_input", f"{label} missing fields: {', '.join(missing)}")
    if extra:
        raise ReviewError(400, "invalid_input", f"{label} has unknown fields: {', '.join(extra)}")


def _bounded_string(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewError(400, "invalid_input", f"{label} must be a non-empty string")
    if len(value) > maximum:
        raise ReviewError(400, "invalid_input", f"{label} exceeds {maximum} characters")
    if "\x00" in value:
        raise ReviewError(400, "invalid_input", f"{label} contains a NUL character")
    return value


def _portable_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not PORTABLE_ID_RE.fullmatch(value):
        raise ReviewError(400, "invalid_input", f"{label} has an invalid portable identifier")
    return value


def _safe_relative_path(value: Any, label: str) -> PurePosixPath:
    try:
        return root_writer._validate_relative_path(value, label)
    except WriterError as exc:
        raise ReviewError(400, "invalid_input", str(exc)) from exc


def _under(path: PurePosixPath, prefix: PurePosixPath) -> bool:
    return path == prefix or path.parts[: len(prefix.parts)] == prefix.parts


def _is_review_evidence_file(path: PurePosixPath) -> bool:
    return any(path != root and _under(path, root) for root in REVIEW_EVIDENCE_ROOTS)


def _read_json_bytes(content: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewError(409, "invalid_state", f"Cannot parse {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewError(409, "invalid_state", f"{label} must be a JSON object")
    return value


def _strict_request_json(content: bytes) -> dict[str, Any]:
    """Decode one HTTP JSON object without accepting ambiguous duplicate keys."""

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
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ReviewError(400, "invalid_json", "Request body must be strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ReviewError(400, "invalid_input", "Request body must be a JSON object")
    return value


def _project_start_as_review_error(error: ProjectStartError) -> ReviewError:
    """Keep project-start internals out of the local dashboard response."""

    public = {
        "invalid_project_id": (
            400,
            "invalid_project_id",
            "The project selection is invalid.",
        ),
        "project_not_found": (
            404,
            "project_not_found",
            "Selected project does not exist.",
        ),
        "stale_project_profile": (
            409,
            "stale_project_profile",
            "This project changed. Refresh the desk before creating a brief.",
        ),
        "invalid_project_brief": (
            400,
            "invalid_project_brief",
            "Complete the required project brief fields and try again.",
        ),
    }
    status, code, message = public.get(
        error.code,
        (
            409,
            "project_start_unavailable",
            "Project start is unavailable. Refresh the desk and try again.",
        ),
    )
    return ReviewError(status, code, message)


def _goal_loop_as_review_error(error: GoalLoopError) -> ReviewError:
    """Keep local loop implementation details outside the HTTP response."""

    public = {
        "brief_required": (
            409,
            "brief_required",
            "Set a project brief before starting the local check.",
        ),
        "stale_project_profile": (
            409,
            "stale_project_profile",
            "This project changed. Refresh the desk before starting the local check.",
        ),
        "invalid_goal_loop": (400, "invalid_goal_loop", "The local check request is incomplete."),
        "project_not_found": (404, "project_not_found", "Selected project does not exist."),
    }
    status, code, message = public.get(
        error.code,
        (409, "goal_loop_unavailable", "The local check is unavailable. Refresh the desk and try again."),
    )
    return ReviewError(status, code, message)


def validate_pending_manifest_boundary(
    value: dict[str, Any], profile: dict[str, Any]
) -> dict[str, Any]:
    """Validate the manual v1 pending-manifest boundary.

    This is the single future schema integration hook. The returned object is a
    defensive copy with the same canonical field names consumed by the dashboard.
    """

    required = {
        "review_item_version",
        "project_id",
        "project_profile_revision",
        "artifact_id",
        "artifact_revision",
        "artifact_path",
        "artifact_sha256",
        "title",
        "type",
        "preview",
        "evidence",
        "quality_checks",
    }
    _exact_keys(value, required, set(), "pending review manifest")
    if value["review_item_version"] != REVIEW_ITEM_VERSION:
        raise ReviewError(409, "invalid_state", "Unsupported review item version")
    if value["project_id"] != profile["project_id"]:
        raise ReviewError(409, "cross_project", "Review item belongs to another project")
    if value["project_profile_revision"] != profile["profile_revision"]:
        raise ReviewError(409, "stale_revision", "Review item uses a stale project profile revision")

    artifact_id = _portable_id(value["artifact_id"], "artifact_id")
    artifact_revision = _portable_id(value["artifact_revision"], "artifact_revision")
    artifact_path = _safe_relative_path(value["artifact_path"], "artifact_path")
    if not _under(artifact_path, PurePosixPath("outbox/artifacts")):
        raise ReviewError(
            409,
            "invalid_state",
            "Reviewed artifacts must live below outbox/artifacts",
        )
    if artifact_path == PurePosixPath("outbox/artifacts"):
        raise ReviewError(409, "invalid_state", "artifact_path must name a file")
    artifact_sha256 = value["artifact_sha256"]
    if not isinstance(artifact_sha256, str) or not SHA256_RE.fullmatch(artifact_sha256):
        raise ReviewError(409, "invalid_state", "artifact_sha256 must be 64 lowercase hex characters")

    title = _bounded_string(value["title"], "title", MAX_TITLE_CHARS)
    item_type = _bounded_string(value["type"], "type", MAX_TYPE_CHARS)
    preview = _bounded_string(value["preview"], "preview", MAX_PREVIEW_CHARS)

    evidence = value["evidence"]
    if not isinstance(evidence, list):
        raise ReviewError(409, "invalid_state", "evidence must be an array")
    checked_evidence: list[dict[str, str]] = []
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            raise ReviewError(409, "invalid_state", f"evidence[{index}] must be an object")
        _exact_keys(item, {"label", "ref"}, set(), f"evidence[{index}]")
        evidence_ref = _safe_relative_path(item["ref"], f"evidence[{index}].ref")
        if not _is_review_evidence_file(evidence_ref):
            raise ReviewError(
                409,
                "invalid_state",
                f"evidence[{index}].ref must name a file below an approved review evidence root",
            )
        checked_evidence.append(
            {
                "label": _bounded_string(item["label"], f"evidence[{index}].label", 240),
                "ref": evidence_ref.as_posix(),
            }
        )

    checks = value["quality_checks"]
    if not isinstance(checks, list):
        raise ReviewError(409, "invalid_state", "quality_checks must be an array")
    checked_checks: list[dict[str, str]] = []
    for index, item in enumerate(checks):
        if not isinstance(item, dict):
            raise ReviewError(409, "invalid_state", f"quality_checks[{index}] must be an object")
        _exact_keys(item, {"name", "status", "detail"}, set(), f"quality_checks[{index}]")
        if item["status"] not in QUALITY_STATUSES:
            raise ReviewError(409, "invalid_state", f"quality_checks[{index}].status is unsupported")
        checked_checks.append(
            {
                "name": _bounded_string(item["name"], f"quality_checks[{index}].name", 240),
                "status": item["status"],
                "detail": _bounded_string(item["detail"], f"quality_checks[{index}].detail", 2_000),
            }
        )

    return {
        "review_item_version": REVIEW_ITEM_VERSION,
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "artifact_id": artifact_id,
        "artifact_revision": artifact_revision,
        "artifact_path": artifact_path.as_posix(),
        "artifact_sha256": artifact_sha256,
        "title": title,
        "type": item_type,
        "preview": preview,
        "evidence": checked_evidence,
        "quality_checks": checked_checks,
    }


class ReviewService:
    """Filesystem-backed review service scoped to one workspace."""

    def __init__(self, workspace: Path, *, broker_database: Path | None = None,
                 broker_permissions: Path | None = None,
                 codex_executable: Path | None = None) -> None:
        self.broker_database = broker_database
        self.broker_permissions = broker_permissions
        try:
            self.workspace = root_writer._validate_workspace(workspace)
        except WriterError as exc:
            raise ReviewError(500, "invalid_workspace", str(exc)) from exc
        if self.broker_database is None and self.broker_permissions is None:
            local_database = self.workspace / "runtime/broker/tasks.sqlite"
            local_permissions = self.workspace / "runtime/permissions.json"
            if local_database.is_file() and local_permissions.is_file():
                self.broker_database = local_database
                self.broker_permissions = local_permissions
        try:
            self.goal_loops = GoalLoopService(self.workspace)
        except GoalLoopError as exc:
            raise _goal_loop_as_review_error(exc) from exc
        self.broker_automation = None
        if self.broker_database is not None and self.broker_permissions is not None:
            try:
                runtime = json.loads(self.broker_permissions.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError, TypeError):
                runtime = {}
            try:
                pipeline_autostart = all(
                    runtime["agents"][agent].get("autostart") is True
                    for agent in ("research", "marketing", "mavery-qa")
                )
            except (KeyError, TypeError):
                pipeline_autostart = False
            try:
                publisher_autostart = (
                    runtime["agents"]["publisher"].get("enabled") is True
                    and runtime["connectors"]["postiz"].get("enabled") is True
                )
            except (KeyError, TypeError):
                publisher_autostart = False
            executable = codex_executable or shutil.which("codex")
            valid_executable = (
                Path(executable) if executable and Path(executable).is_absolute()
                and Path(executable).is_file() else None
            )
            enabled_agents = set()
            if pipeline_autostart and valid_executable is not None:
                enabled_agents.update(("research", "marketing", "mavery-qa"))
            if publisher_autostart:
                enabled_agents.add("publisher")
            if enabled_agents:
                from pipeline.broker_automation import BrokerAutomation
                self.broker_automation = BrokerAutomation(
                    workspace=self.workspace, database=self.broker_database,
                    permissions=self.broker_permissions, executable=valid_executable,
                    enabled_agents=enabled_agents)
                try:
                    self._resume_pipeline_tasks()
                except (BrokerError, sqlite3.Error, OSError) as exc:
                    self.broker_automation.close()
                    self.broker_automation = None
                    raise ReviewError(503, "broker_unavailable",
                                      "Queued broker work could not be resumed") from exc

    def close(self) -> None:
        if self.broker_automation is not None:
            self.broker_automation.close()
        self.goal_loops.close()

    def _resume_pipeline_tasks(self) -> None:
        if self.broker_automation is None or self.broker_database is None or self.broker_permissions is None:
            return
        from pipeline.task_broker import TaskBroker

        profiles = self._profiles()
        broker = TaskBroker(self.broker_database, self.broker_permissions)
        try:
            queued = broker.db.execute('''SELECT project_id, task_id FROM tasks
                WHERE agent_id IN ('research', 'marketing', 'mavery-qa', 'publisher')
                AND status='queued'
                ORDER BY created_at, task_id LIMIT 20''').fetchall()
        finally:
            broker.close()
        for task in queued:
            selected = profiles.get(task["project_id"])
            if selected is not None:
                self.broker_automation.start(
                    project=task["project_id"], task_id=task["task_id"],
                    profile_path=selected[0])

    def broker_status(self, project_id: str) -> dict[str, Any]:
        from pipeline.task_broker import BrokerError, read_status

        self._selected_profile(project_id)
        if self.broker_database is None:
            return {'project_id': project_id, 'state': 'not_configured', 'counts': {},
                    'tasks': [], 'truncated': False}
        try:
            return read_status(self.broker_database, project_id)
        except BrokerError as exc:
            raise ReviewError(503, 'broker_unavailable', str(exc)) from exc

    def setup_status(self) -> dict[str, Any]:
        """Return coarse operating states without exposing paths or configuration."""

        permissions_path = self.workspace / "runtime/permissions.json"
        publication = {"mode": "not_configured", "publisher": "disabled"}
        try:
            if permissions_path.is_symlink() or not permissions_path.is_file():
                raise OSError()
            permissions = json.loads(permissions_path.read_text(encoding="utf-8"))
            mode = permissions["publish"]
            if mode not in {"off", "review", "automatic"}:
                raise ValueError()
            publisher = (
                permissions["agents"]["publisher"].get("enabled") is True
                and permissions["connectors"]["postiz"].get("enabled") is True
            )
            publication = {"mode": mode, "publisher": "enabled" if publisher else "disabled"}
        except (OSError, UnicodeError, ValueError, KeyError, TypeError):
            pass
        try:
            access = remote_access.readiness(self.workspace)
        except remote_access.RemoteAccessError:
            access = {"state": "needs_attention", "provider": "tailscale-serve"}
        return {
            "desk": "running",
            "background": "active" if self.broker_automation is not None else "manual",
            "autostart": macos_autostart.readiness(self.workspace),
            "remote_access": access,
            "publication": publication,
            "host": "must_be_awake",
        }

    def admit_studio_proposal(self, profile_path: Path, profile: dict[str, Any],
                              request: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        """Queue one saved proposal; local HTTP supplies data, never authority."""
        if request.get("kind") != "proposal":
            return result
        if self.broker_database is None or self.broker_permissions is None:
            return result | {"automation": {"state": "not_configured"}}
        from pipeline.broker_ingress import admit_event
        from pipeline.inbox_worker import route_task
        from pipeline.task_broker import BrokerError, TaskBroker

        record = result["record"]
        try:
            broker = TaskBroker(self.broker_database, self.broker_permissions)
            try:
                task = admit_event(broker, {
                    "version": 1,
                    "project_id": profile["project_id"],
                    "agent_id": "inbox",
                    "origin": "operator",
                    "actor_id": "local-operator",
                    "input_ref": record["artifact_ref"],
                    "input_sha256": record["artifact_sha256"],
                    "not_before": 0,
                    "max_attempts": 1,
                    "identity": {
                        "transport": "project-desk-loopback",
                        "account_id": "local-os-user",
                        "event_id": request["request_id"],
                    },
                })
                if task["status"] == "queued":
                    routed = route_task(
                        broker, project=profile["project_id"],
                        task_id=task["task_id"], workspace=self.workspace,
                        profile_path=profile_path)
                    child_status = routed["research_status"]
                    automation = {
                        "state": "routed", "task_id": routed["research_task_id"],
                        "agent_id": "research",
                        "inbox_task_id": routed["inbox_task_id"],
                    }
                elif task["status"] == "completed":
                    current = task
                    expected_agents = iter(("research", "marketing", "mavery-qa"))
                    while current["status"] == "completed" and current["agent_id"] != "mavery-qa":
                        expected = next(expected_agents, None)
                        children = broker.db.execute('''SELECT task_id, agent_id, status FROM tasks
                            WHERE project_id=? AND parent_task_id=? ORDER BY created_at, task_id''',
                            (profile["project_id"], current["task_id"])).fetchall()
                        if len(children) != 1 or children[0]["agent_id"] != expected:
                            raise BrokerError("Completed pipeline task lacks one expected handoff")
                        current = children[0]
                    child_status = current["status"]
                    automation = {
                        "state": child_status if child_status != "queued" else "routed",
                        "task_id": current["task_id"],
                        "agent_id": current["agent_id"], "inbox_task_id": task["task_id"],
                    }
                else:
                    raise BrokerError("Inbox task is not safely routable")
                if self.broker_automation is not None and child_status == "queued":
                    if self.broker_automation.start(
                            project=profile["project_id"], task_id=automation["task_id"],
                            profile_path=profile_path):
                        automation["state"] = "processing"
            finally:
                broker.close()
        except (BrokerError, WriterError) as exc:
            raise ReviewError(503, "broker_unavailable",
                              "Idea was saved, but its background route could not finish. Retry safely.") from exc
        return result | {"automation": automation}

    def _profiles(self) -> dict[str, tuple[Path, dict[str, Any]]]:
        projects_root = self.workspace / "projects"
        if not projects_root.is_dir() or projects_root.is_symlink():
            raise ReviewError(500, "invalid_workspace", "Workspace projects directory is unavailable")
        profiles: dict[str, tuple[Path, dict[str, Any]]] = {}
        try:
            packages = sorted(projects_root.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise ReviewError(500, "invalid_workspace", f"Cannot inspect projects: {exc}") from exc
        for package in packages:
            if package.is_symlink() or not package.is_dir():
                continue
            profile_path = package / "project.json"
            if profile_path.is_symlink() or not profile_path.is_file():
                continue
            try:
                profile = root_writer.load_project_profile(profile_path)
                root_writer._validate_profile_package(self.workspace, profile_path, profile)
            except WriterError as exc:
                raise ReviewError(500, "invalid_project_profile", str(exc)) from exc
            project_id = profile["project_id"]
            if project_id in profiles:
                raise ReviewError(500, "duplicate_project", f"Duplicate project_id: {project_id}")
            profiles[project_id] = (profile_path, profile)
        return profiles

    def projects(self) -> list[dict[str, str]]:
        result = []
        for project_id, (_, profile) in self._profiles().items():
            display_name = profile.get("display_name", project_id)
            if not isinstance(display_name, str) or not display_name.strip():
                raise ReviewError(500, "invalid_project_profile", "display_name must be a non-empty string")
            result.append(
                {
                    "project_id": project_id,
                    "display_name": display_name,
                    "project_profile_revision": profile["profile_revision"],
                }
            )
        return sorted(result, key=lambda item: (item["display_name"].casefold(), item["project_id"]))

    def project_desk(self, project_id: Any) -> dict[str, Any]:
        """Return the customer-safe project-start read model for one project."""

        try:
            desk = ProjectStartService(self.workspace).project_desk(project_id)
        except ProjectStartError as exc:
            raise _project_start_as_review_error(exc) from exc
        profile_path, profile = self._selected_profile(project_id)
        automation: dict[str, Any] = {
            "action": "unconfigured", "run_id": None, "route_id": None,
            "active_roles": [], "final_title": None, "reason": None,
            "progress": None,
        }
        if "_agent_registry" in profile:
            from pipeline.autopilot import inspect_autopilot

            try:
                view = inspect_autopilot(self.workspace, profile_path)
                automation.update({
                    "action": view["action"], "run_id": view.get("run_id"),
                    "route_id": view.get("route_id"), "final_title": view.get("title"),
                    "progress": view.get("progress"),
                    "active_roles": [{
                        "role_id": order["task"]["assigned_role"],
                        "label": order["role_contract"]["display_name"],
                        "step_id": order["task"]["step_id"],
                    } for order in view["work_orders"]],
                })
                if view["action"] == "settled":
                    automation["reason"] = "This project brief has completed its cycle."
                elif view["action"] == "blocked":
                    automation["reason"] = "The current work needs an input or a revision before it can continue."
            except (ValueError, OSError):
                automation.update(action="blocked", reason="The current project work could not be verified. Its saved results remain available for inspection.")
        else:
            automation["reason"] = "Automation is not configured for this project yet."
        desk["automation"] = automation
        desk["foundation"] = self._foundation(profile_path, profile)
        return desk

    def _foundation_bytes(self, relative: PurePosixPath, maximum: int) -> bytes:
        """Read a bounded regular file without following any path symlinks."""

        workspace_fd = os.open(self.workspace, root_writer._directory_flags())
        try:
            parent_fd = root_writer._open_directory_chain(
                workspace_fd, relative.parts[:-1], create=False, label="foundation source"
            )
        finally:
            os.close(workspace_fd)
        try:
            file_fd = os.open(
                relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent_fd,
            )
        finally:
            os.close(parent_fd)
        try:
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise ValueError("Foundation sources must be regular files")
            with os.fdopen(file_fd, "rb", closefd=False) as source:
                content = source.read(maximum + 1)
            if len(content) > maximum:
                raise ValueError("Foundation source exceeds its display limit")
            return content
        finally:
            os.close(file_fd)

    def _foundation(self, profile_path: Path, profile: dict[str, Any]) -> list[dict[str, Any]]:
        """Project-configured reference documents, never an approval or work queue.

        Trusted local configuration pins the exact Markdown bytes and descriptive
        labels. A changed source fails closed instead of retaining a stale label.
        No document is selected from an HTTP parameter or another project's tree.
        """

        package = PurePosixPath(profile_path.parent.relative_to(self.workspace).as_posix())
        unavailable = {
            "id": "foundation", "title": "Project foundation", "status": "unavailable",
            "status_label": "Reference refresh needed",
            "summary": "A configured reference changed or could not be verified. Refresh its reference configuration before relying on this summary. No approval state changed.",
            "documents": [],
        }
        try:
            raw = self._foundation_bytes(package / "foundation.json", 32_768)
        except FileNotFoundError:
            return []
        except (OSError, ValueError, WriterError):
            return [unavailable]
        try:
            config = _strict_request_json(raw)
            if (config.get("project_id") != profile["project_id"]
                    or config.get("project_profile_revision") != profile["profile_revision"]):
                raise ValueError("Foundation configuration belongs to another revision")
            categories = config["categories"]
            if not isinstance(categories, list) or not 1 <= len(categories) <= 8:
                raise ValueError("Invalid foundation categories")
            output = []
            seen = set()
            for category in categories:
                category_id = _portable_id(category["id"], "foundation category")
                if category_id in seen:
                    raise ValueError("Duplicate foundation category")
                seen.add(category_id)
                item = {
                    "id": category_id,
                    "title": _bounded_string(category["title"], "foundation title", 120),
                    "status": "reference",
                    "status_label": _bounded_string(category["status_label"], "source label", 120),
                    "summary": _bounded_string(category["summary"], "source summary", 1_000),
                    "documents": [],
                }
                try:
                    documents = category["documents"]
                    if not isinstance(documents, list) or not 1 <= len(documents) <= 4:
                        raise ValueError("Invalid foundation documents")
                    for document in documents:
                        relative = _safe_relative_path(document["path"], "foundation path")
                        if (relative.suffix != ".md"
                                or any(part.startswith(".") for part in relative.parts)
                                or (relative.parts[0] == "projects" and not _under(relative, package))):
                            raise ValueError("Foundation source is outside the selected scope")
                        content = self._foundation_bytes(relative, 65_536)
                        if _sha256(content) != document["sha256"]:
                            raise ValueError("Foundation source changed")
                        item["documents"].append({
                            "label": _bounded_string(document["label"], "document label", 120),
                            "markdown": content.decode("utf-8"),
                        })
                except (OSError, ValueError, KeyError, TypeError, WriterError):
                    item.update(status="unavailable", status_label=unavailable["status_label"],
                                summary=unavailable["summary"], documents=[])
                output.append(item)
            return output
        except (ValueError, KeyError, TypeError, WriterError):
            return [unavailable]

    def create_project_brief(self, value: Any) -> dict[str, Any]:
        """Persist an immutable operator brief; this does not queue or publish work."""

        try:
            return ProjectStartService(self.workspace).create_start_brief(value)
        except ProjectStartError as exc:
            raise _project_start_as_review_error(exc) from exc

    def goal_loop(self, project_id: Any) -> dict[str, Any]:
        try:
            return self.goal_loops.status(project_id)
        except GoalLoopError as exc:
            raise _goal_loop_as_review_error(exc) from exc

    def research_feeds(self, project_id: Any) -> dict[str, Any]:
        try:
            response = read_feed_intake(self.workspace, project_id)
            try:
                response['synthesis'] = read_synthesis(self.workspace, project_id) if response['result'] else None
            except (ValueError, OSError, GoalLoopError):
                response['synthesis'] = {'status': 'unavailable', 'analysis': None}
            return response
        except GoalLoopError as exc:
            raise _goal_loop_as_review_error(exc) from exc

    def start_goal_loop(self, value: Any) -> dict[str, Any]:
        try:
            return self.goal_loops.start(value)
        except GoalLoopError as exc:
            raise _goal_loop_as_review_error(exc) from exc

    def _selected_profile(self, project_id: Any) -> tuple[Path, dict[str, Any]]:
        project_id = _portable_id(project_id, "project_id")
        selected = self._profiles().get(project_id)
        if selected is None:
            raise ReviewError(404, "project_not_found", "Selected project does not exist")
        return selected

    def _state_root(self, profile: dict[str, Any]) -> Path:
        return self.workspace.joinpath(*profile["_state_root"].parts)

    def _read_state_bytes(
        self,
        profile: dict[str, Any],
        relative: PurePosixPath,
        *,
        missing_ok: bool = False,
    ) -> bytes | None:
        state_root = self._state_root(profile)
        try:
            state_fd = os.open(state_root, root_writer._directory_flags())
        except FileNotFoundError:
            if missing_ok:
                return None
            raise ReviewError(409, "invalid_state", "Project state does not exist")
        except OSError as exc:
            raise ReviewError(409, "invalid_state", f"Cannot open project state safely: {exc}") from exc
        try:
            return root_writer._read_relative_bytes(
                state_fd, relative, missing_ok=missing_ok
            )
        except WriterError as exc:
            raise ReviewError(409, "invalid_state", str(exc)) from exc
        finally:
            os.close(state_fd)

    def _pending_paths(self, profile: dict[str, Any]) -> list[PurePosixPath]:
        state_root = self._state_root(profile)
        pending_root = state_root / "outbox/pending"
        if not pending_root.exists():
            return []
        if pending_root.is_symlink() or not pending_root.is_dir():
            raise ReviewError(409, "invalid_state", "outbox/pending must be a real directory")
        paths: list[PurePosixPath] = []
        for directory, names, filenames in os.walk(pending_root, followlinks=False):
            directory_path = Path(directory)
            for name in names:
                if (directory_path / name).is_symlink():
                    raise ReviewError(409, "invalid_state", "Symlink directories are forbidden in outbox/pending")
            for name in filenames:
                candidate = directory_path / name
                if candidate.is_symlink():
                    raise ReviewError(409, "invalid_state", "Symlink files are forbidden in outbox/pending")
                if candidate.suffix != ".json":
                    continue
                relative = PurePosixPath(candidate.relative_to(state_root).as_posix())
                paths.append(relative)
        return sorted(paths, key=lambda item: item.as_posix())

    def _action_relative(self, artifact_id: str, artifact_revision: str) -> PurePosixPath:
        return PurePosixPath("outbox/review-actions") / artifact_id / f"{artifact_revision}.json"

    def _validated_action_record(
        self, value: dict[str, Any], profile: dict[str, Any]
    ) -> dict[str, Any]:
        fields = {
            "review_action_version",
            "action_id",
            "project_id",
            "project_profile_revision",
            "artifact_id",
            "artifact_revision",
            "artifact_sha256",
            "action",
            "actor_type",
            "recorded_at",
            "note",
            "reason",
            "rework_requested",
        }
        _exact_keys(value, fields, set(), "review action record")
        if value["review_action_version"] != REVIEW_ACTION_VERSION:
            raise ReviewError(409, "invalid_state", "Unsupported review action version")
        try:
            parsed_action_id = uuid.UUID(str(value["action_id"]))
        except (ValueError, AttributeError) as exc:
            raise ReviewError(409, "invalid_state", "Stored action_id is invalid") from exc
        if str(parsed_action_id) != value["action_id"]:
            raise ReviewError(409, "invalid_state", "Stored action_id is not canonical")
        if value["project_id"] != profile["project_id"]:
            raise ReviewError(409, "cross_project", "Stored action belongs to another project")
        if value["project_profile_revision"] != profile["profile_revision"]:
            raise ReviewError(409, "stale_revision", "Stored action uses a stale project profile revision")
        _portable_id(value["artifact_id"], "artifact_id")
        _portable_id(value["artifact_revision"], "artifact_revision")
        if not isinstance(value["artifact_sha256"], str) or not SHA256_RE.fullmatch(value["artifact_sha256"]):
            raise ReviewError(409, "invalid_state", "Stored artifact hash is invalid")
        action = value["action"]
        if action not in ACTION_TO_STATUS:
            raise ReviewError(409, "invalid_state", "Stored review action is unsupported")
        if value["actor_type"] != "human":
            raise ReviewError(
                409, "invalid_state", "Stored action actor_type must be human"
            )
        recorded_at = value["recorded_at"]
        if not isinstance(recorded_at, str) or not RFC3339_UTC_RE.fullmatch(
            recorded_at
        ):
            raise ReviewError(
                409,
                "invalid_state",
                "Stored recorded_at must be RFC3339 UTC seconds",
            )
        try:
            dt.datetime.strptime(recorded_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise ReviewError(
                409, "invalid_state", "Stored recorded_at is not a real UTC date-time"
            ) from exc
        expected_note = isinstance(value["note"], str) and bool(value["note"].strip())
        expected_reason = isinstance(value["reason"], str) and bool(value["reason"].strip())
        if action == "approve" and (value["note"] is not None or value["reason"] is not None):
            raise ReviewError(409, "invalid_state", "Stored approval has note or reason")
        if action == "decline" and (not expected_reason or value["note"] is not None):
            raise ReviewError(409, "invalid_state", "Stored decline reason is invalid")
        if action == "note" and (not expected_note or value["reason"] is not None):
            raise ReviewError(409, "invalid_state", "Stored rework note is invalid")
        if expected_note and len(value["note"]) > MAX_NOTE_CHARS:
            raise ReviewError(409, "invalid_state", "Stored rework note is too long")
        if expected_reason and len(value["reason"]) > MAX_NOTE_CHARS:
            raise ReviewError(409, "invalid_state", "Stored decline reason is too long")
        if not isinstance(value["rework_requested"], bool) or value[
            "rework_requested"
        ] != (action == "note"):
            raise ReviewError(409, "invalid_state", "Stored rework signal is inconsistent")
        return dict(value)

    def _existing_action(
        self,
        profile: dict[str, Any],
        artifact_id: str,
        artifact_revision: str,
    ) -> dict[str, Any] | None:
        # Root Writer publishes the artifact before its completion receipt. Its
        # workspace lock prevents readers from mistaking that short interval (or
        # a failed partial write) for a canonical terminal action.
        with root_writer._workspace_lock(self.workspace):
            relative = self._action_relative(artifact_id, artifact_revision)
            content = self._read_state_bytes(profile, relative, missing_ok=True)
            if content is None:
                return None
            record = self._validated_action_record(
                _read_json_bytes(content, "review action record"), profile
            )
            if (
                record["artifact_id"] != artifact_id
                or record["artifact_revision"] != artifact_revision
            ):
                raise ReviewError(
                    409,
                    "invalid_state",
                    "Stored action identity does not match its immutable path",
                )
            expected_identity = "\n".join(
                [
                    record["project_id"],
                    record["project_profile_revision"],
                    record["artifact_id"],
                    record["artifact_revision"],
                    record["artifact_sha256"],
                ]
            )
            if record["action_id"] != str(
                uuid.uuid5(ACTION_NAMESPACE, expected_identity)
            ):
                raise ReviewError(
                    409,
                    "invalid_state",
                    "Stored action_id does not match its identity",
                )
            self._require_completed_action_receipt(profile, record)
            return record

    def _load_review_item(
        self, profile: dict[str, Any], manifest_relative: PurePosixPath
    ) -> dict[str, Any]:
        content = self._read_state_bytes(profile, manifest_relative)
        assert content is not None
        manifest = validate_pending_manifest_boundary(
            _read_json_bytes(content, "pending review manifest"), profile
        )
        artifact_relative = PurePosixPath(manifest["artifact_path"])
        artifact = self._read_state_bytes(profile, artifact_relative)
        assert artifact is not None
        actual_sha256 = _sha256(artifact)
        if actual_sha256 != manifest["artifact_sha256"]:
            raise ReviewError(
                409,
                "artifact_hash_mismatch",
                f"Artifact bytes do not match the pending manifest: {manifest['artifact_id']}@{manifest['artifact_revision']}",
            )
        try:
            artifact_content = artifact.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReviewError(
                422,
                "render_adapter_required",
                (
                    "Verified artifact bytes are not UTF-8 text; an exact-byte "
                    "render adapter is required before human review"
                ),
            ) from exc
        resolved_evidence: list[dict[str, str]] = []
        for item in manifest["evidence"]:
            evidence_bytes = self._read_state_bytes(
                profile, PurePosixPath(item["ref"])
            )
            assert evidence_bytes is not None
            resolved_evidence.append(
                {
                    "label": item["label"],
                    "ref": item["ref"],
                    "sha256": _sha256(evidence_bytes),
                }
            )

        action = self._existing_action(
            profile, manifest["artifact_id"], manifest["artifact_revision"]
        )
        if action is not None and action["artifact_sha256"] != actual_sha256:
            raise ReviewError(
                409,
                "artifact_hash_mismatch",
                "Stored action does not bind the current immutable artifact bytes",
            )
        status = "pending" if action is None else ACTION_TO_STATUS[action["action"]]
        return {
            "review_item_version": manifest["review_item_version"],
            "project_id": manifest["project_id"],
            "project_profile_revision": manifest["project_profile_revision"],
            "artifact_id": manifest["artifact_id"],
            "artifact_revision": manifest["artifact_revision"],
            "artifact_sha256": actual_sha256,
            "title": manifest["title"],
            "type": manifest["type"],
            "status": status,
            "preview": manifest["preview"],
            "artifact_content": artifact_content,
            "evidence": resolved_evidence,
            "quality_checks": manifest["quality_checks"],
            "revision": {
                "id": manifest["artifact_revision"],
                "sha256": actual_sha256,
                "artifact_path": manifest["artifact_path"],
            },
            "action": action,
        }

    def reviews(self, project_id: Any) -> dict[str, Any]:
        _, profile = self._selected_profile(project_id)
        reviews: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for path in self._pending_paths(profile):
            item = self._load_review_item(profile, path)
            identity = (item["artifact_id"], item["artifact_revision"])
            if identity in seen:
                raise ReviewError(409, "duplicate_review", f"Duplicate pending review: {identity[0]}@{identity[1]}")
            seen.add(identity)
            reviews.append(item)
        reviews.sort(key=lambda item: (item["title"].casefold(), item["artifact_id"], item["artifact_revision"]))
        return {
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
            "reviews": reviews,
        }

    def evidence(
        self, project_id: Any, evidence_ref: Any, expected_sha256: Any
    ) -> dict[str, Any]:
        """Return verified UTF-8 evidence from one selected project state tree."""

        _, profile = self._selected_profile(project_id)
        relative = _safe_relative_path(evidence_ref, "ref")
        if not _is_review_evidence_file(relative):
            raise ReviewError(
                400,
                "invalid_input",
                "ref must name a file below an approved review evidence root",
            )
        if not isinstance(expected_sha256, str) or not SHA256_RE.fullmatch(
            expected_sha256
        ):
            raise ReviewError(
                400, "invalid_input", "sha256 must be 64 lowercase hex characters"
            )

        content = self._read_state_bytes(profile, relative, missing_ok=True)
        if content is None:
            raise ReviewError(404, "evidence_not_found", "Evidence file does not exist")
        actual_sha256 = _sha256(content)
        if actual_sha256 != expected_sha256:
            raise ReviewError(
                409,
                "evidence_hash_mismatch",
                "Evidence bytes changed after the review queue was loaded",
            )
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReviewError(
                422,
                "render_adapter_required",
                (
                    "Verified evidence bytes are not UTF-8 text; an exact-byte "
                    "render adapter is required before human review"
                ),
            ) from exc
        return {
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
            "evidence_ref": relative.as_posix(),
            "evidence_sha256": actual_sha256,
            "byte_length": len(content),
            "content": decoded,
        }

    def review_material(
        self,
        project_id: Any,
        packet_ref: Any,
        packet_sha256: Any,
        material_id: Any,
        content_sha256: Any,
    ) -> dict[str, Any]:
        """Return exact media only when an immutable review packet binds it."""
        evidence = self.evidence(project_id, packet_ref, packet_sha256)
        try:
            packet = json.loads(evidence["content"])
        except (ValueError, TypeError, RecursionError) as exc:
            raise ReviewError(409, "invalid_state", "Material review packet is invalid") from exc
        expected_packet = {
            "material_packet_version", "project_id", "project_profile_revision",
            "channel", "materials",
        }
        if not isinstance(packet, dict) or set(packet) != expected_packet:
            raise ReviewError(409, "invalid_state", "Material review packet fields are invalid")
        if (
            packet["material_packet_version"] != "1.0"
            or packet["project_id"] != evidence["project_id"]
            or packet["project_profile_revision"] != evidence["project_profile_revision"]
            or not isinstance(packet["materials"], list)
            or len(packet["materials"]) > 10
        ):
            raise ReviewError(409, "invalid_state", "Material review packet identity is invalid")
        packet_ids = [
            item.get("material_id") for item in packet["materials"]
            if isinstance(item, dict) and isinstance(item.get("material_id"), str)
        ]
        if len(packet_ids) != len(packet["materials"]) or len(set(packet_ids)) != len(packet_ids):
            raise ReviewError(409, "invalid_state", "Material review packet identity is invalid")
        descriptor = next(
            (item for item in packet["materials"]
             if isinstance(item, dict) and item.get("material_id") == material_id),
            None,
        )
        fields = {
            "material_id", "record_ref", "record_sha256", "name", "mime_type",
            "size_bytes", "content_sha256", "processing_status",
        }
        if descriptor is None or set(descriptor) != fields:
            raise ReviewError(404, "material_not_found", "Material is not bound to this review")
        if descriptor["content_sha256"] != content_sha256:
            raise ReviewError(409, "material_hash_mismatch", "Material hash differs from the review packet")
        _, profile = self._selected_profile(project_id)
        try:
            asset = studio.material_asset(self.workspace, profile, material_id)
        except studio.StudioError as exc:
            raise ReviewError(exc.status, exc.code, str(exc)) from exc
        for key in fields:
            if asset.get(key) != descriptor[key]:
                raise ReviewError(409, "material_hash_mismatch", "Material differs from the review packet")
        return {
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
            "packet_ref": evidence["evidence_ref"],
            "packet_sha256": evidence["evidence_sha256"],
            **descriptor,
            "content_base64": base64.b64encode(asset["content"]).decode("ascii"),
        }

    def _validate_action_input(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ReviewError(400, "invalid_input", "Review action body must be an object")
        base = {
            "action",
            "project_id",
            "project_profile_revision",
            "artifact_id",
            "artifact_revision",
            "artifact_sha256",
        }
        action = value.get("action")
        if action not in ACTION_TO_STATUS:
            raise ReviewError(400, "invalid_input", "action must be approve, decline, or note")
        if action == "approve":
            _exact_keys(value, base, set(), "review action")
            note = None
            reason = None
        elif action == "decline":
            _exact_keys(value, base | {"reason"}, set(), "review action")
            reason = _bounded_string(value["reason"], "reason", MAX_NOTE_CHARS)
            note = None
        else:
            _exact_keys(value, base | {"note"}, set(), "review action")
            note = _bounded_string(value["note"], "note", MAX_NOTE_CHARS)
            reason = None
        project_id = _portable_id(value["project_id"], "project_id")
        profile_revision = _portable_id(value["project_profile_revision"], "project_profile_revision")
        artifact_id = _portable_id(value["artifact_id"], "artifact_id")
        artifact_revision = _portable_id(value["artifact_revision"], "artifact_revision")
        artifact_sha256 = value["artifact_sha256"]
        if not isinstance(artifact_sha256, str) or not SHA256_RE.fullmatch(artifact_sha256):
            raise ReviewError(400, "invalid_input", "artifact_sha256 must be 64 lowercase hex characters")
        return {
            "action": action,
            "project_id": project_id,
            "project_profile_revision": profile_revision,
            "artifact_id": artifact_id,
            "artifact_revision": artifact_revision,
            "artifact_sha256": artifact_sha256,
            "note": note,
            "reason": reason,
        }

    def _canonical_action_record(
        self, action: dict[str, Any], *, recorded_at: str | None = None
    ) -> dict[str, Any]:
        identity = "\n".join(
            [
                action["project_id"],
                action["project_profile_revision"],
                action["artifact_id"],
                action["artifact_revision"],
                action["artifact_sha256"],
            ]
        )
        action_id = str(uuid.uuid5(ACTION_NAMESPACE, identity))
        if recorded_at is None:
            recorded_at = (
                dt.datetime.now(dt.timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            )
        return {
            "review_action_version": REVIEW_ACTION_VERSION,
            "action_id": action_id,
            "project_id": action["project_id"],
            "project_profile_revision": action["project_profile_revision"],
            "artifact_id": action["artifact_id"],
            "artifact_revision": action["artifact_revision"],
            "artifact_sha256": action["artifact_sha256"],
            "action": action["action"],
            "actor_type": "human",
            "recorded_at": recorded_at,
            "note": action["note"],
            "reason": action["reason"],
            "rework_requested": action["action"] == "note",
        }

    def _same_action_semantics(
        self, existing: dict[str, Any], proposed: dict[str, Any]
    ) -> bool:
        fields = {
            "review_action_version",
            "action_id",
            "project_id",
            "project_profile_revision",
            "artifact_id",
            "artifact_revision",
            "artifact_sha256",
            "action",
            "actor_type",
            "note",
            "reason",
            "rework_requested",
        }
        return all(existing.get(field) == proposed.get(field) for field in fields)

    def _writer_request(
        self, profile: dict[str, Any], record: dict[str, Any]
    ) -> dict[str, Any]:
        content = _canonical_json(record) + b"\n"
        relative = self._action_relative(
            record["artifact_id"], record["artifact_revision"]
        )
        return {
            "writer_request_version": root_writer.REQUEST_VERSION,
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
            "run_id": f"review-{record['action_id'].replace('-', '')[:20]}",
            "idempotency_key": record["action_id"],
            "requested_by": profile["root_role_id"],
            "writes": [
                {
                    "path": relative.as_posix(),
                    "mode": "create",
                    "content": content.decode("utf-8"),
                    "content_sha256": _sha256(content),
                    "expected_sha256": None,
                    "media_type": "application/json",
                }
            ],
        }

    def _require_completed_action_receipt(
        self, profile: dict[str, Any], record: dict[str, Any]
    ) -> None:
        request = self._writer_request(profile, record)
        request_sha256 = _sha256(_canonical_json(request))
        receipt_relative = root_writer._receipt_relative(profile, request)
        state_root = self._state_root(profile)
        try:
            state_fd = os.open(state_root, root_writer._directory_flags())
        except OSError as exc:
            raise ReviewError(
                409, "invalid_state", f"Cannot open project state safely: {exc}"
            ) from exc
        try:
            try:
                receipt = root_writer._idempotent_receipt(
                    receipt_relative,
                    request_sha256,
                    state_fd,
                    request,
                )
            except WriterError as exc:
                raise ReviewError(
                    409,
                    "invalid_state",
                    f"Review action lacks a valid Root Writer receipt: {exc}",
                ) from exc
        finally:
            os.close(state_fd)
        if receipt is None:
            raise ReviewError(
                409,
                "invalid_state",
                "Review action lacks a completed Root Writer receipt",
            )

    def _write_action(
        self,
        profile_path: Path,
        profile: dict[str, Any],
        record: dict[str, Any],
    ) -> None:
        request = self._writer_request(profile, record)
        request_bytes = _canonical_json(request)
        request_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix="growth-review-request-", suffix=".json", delete=False
            ) as handle:
                handle.write(request_bytes)
                handle.flush()
                os.fsync(handle.fileno())
                request_path = Path(handle.name)
            root_writer.apply_request(self.workspace, profile_path, request_path)
        finally:
            if request_path is not None:
                try:
                    request_path.unlink()
                except FileNotFoundError:
                    pass

    def review_action(self, value: Any) -> tuple[dict[str, Any], bool]:
        action = self._validate_action_input(value)
        profile_path, profile = self._selected_profile(action["project_id"])
        if action["project_profile_revision"] != profile["profile_revision"]:
            raise ReviewError(409, "stale_revision", "Action uses a stale project profile revision")

        selected: dict[str, Any] | None = None
        for item in self.reviews(action["project_id"])["reviews"]:
            if (
                item["artifact_id"] == action["artifact_id"]
                and item["artifact_revision"] == action["artifact_revision"]
            ):
                selected = item
                break
        if selected is None:
            raise ReviewError(404, "review_not_found", "Exact review revision was not found in the selected project")
        if selected["artifact_sha256"] != action["artifact_sha256"]:
            raise ReviewError(409, "artifact_hash_mismatch", "Action hash does not match the actual artifact bytes")

        record = self._canonical_action_record(action)
        existing = selected["action"]
        if existing is not None:
            if self._same_action_semantics(existing, record):
                return existing, True
            raise ReviewError(409, "action_conflict", "This exact artifact revision already has a terminal action")

        try:
            self._write_action(profile_path, profile, record)
        except WriterError as exc:
            # Close the check/write race through the canonical immutable record.
            existing = self._existing_action(
                profile, action["artifact_id"], action["artifact_revision"]
            )
            if existing is not None and self._same_action_semantics(
                existing, record
            ):
                return existing, True
            if existing is not None:
                raise ReviewError(409, "action_conflict", "This exact artifact revision already has a terminal action") from exc
            raise ReviewError(409, "writer_rejected", str(exc)) from exc
        return record, False

    def finalize_review_action(self, record: dict[str, Any]) -> dict[str, Any]:
        """Reconcile, package, then queue only an explicitly enabled publisher."""
        if record["action"] != "approve":
            return {"status": "not_applicable", "release_package": None,
                    "delivery_status": "not_applicable"}
        publication_target = None
        if self.broker_database is not None and self.broker_permissions is not None:
            from pipeline.broker_writer import reconcile_review
            from pipeline.task_broker import BrokerError, TaskBroker

            broker = TaskBroker(self.broker_database, self.broker_permissions)
            try:
                exists = broker.db.execute(
                    "SELECT 1 FROM tasks WHERE project_id=? AND task_id=?",
                    (record["project_id"], record["artifact_id"]),
                ).fetchone()
                if exists is not None:
                    reconcile_review(
                        broker, self, project=record["project_id"],
                        task=record["artifact_id"],
                    )
                    chain = []
                    task = broker.get(record["project_id"], record["artifact_id"])
                    for _ in range(4):
                        chain.append(task)
                        if task["parent_task_id"] is None:
                            break
                        task = broker.get(record["project_id"], task["parent_task_id"])
                    if [item["agent_id"] for item in chain] == [
                        "mavery-qa", "marketing", "research", "inbox"
                    ]:
                        proposal_ref = PurePosixPath(chain[-1]["input_ref"])
                        if (proposal_ref.parts[:2] == ("records", "studio")
                                and len(proposal_ref.parts) == 3
                                and proposal_ref.suffix == ".json"):
                            _, profile = self._selected_profile(record["project_id"])
                            view = studio.read(self.workspace, profile)
                            proposal_id = proposal_ref.stem
                            proposal = next((item for item in view["proposals"]
                                             if item["id"] == proposal_id), None)
                            slot = next((item for item in view["slots"]
                                         if item["proposal_id"] == proposal_id), None)
                            if proposal is not None:
                                publication_target = {
                                    "platform": proposal["channel"],
                                    "scheduled_at": slot["planned_at"] if slot else None,
                                }
            except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
                return {"status": "needs_attention", "release_package": None,
                        "delivery_status": "needs_attention"}
            finally:
                broker.close()
        try:
            from pipeline.release_core import ReleaseError, create_release_package

            packaged = create_release_package(
                self.workspace,
                self._selected_profile(record["project_id"])[0],
                artifact_id=record["artifact_id"],
                artifact_revision=record["artifact_revision"],
                artifact_sha256=record["artifact_sha256"],
            )
        except (ReleaseError, OSError, ValueError):
            return {"status": "needs_attention", "release_package": None,
                    "delivery_status": "needs_attention"}
        result = {"status": "packaged", "release_package": packaged["release_package"],
                  "delivery_status": "not_enabled"}
        try:
            permissions = json.loads(
                (self.workspace / "runtime/permissions.json").read_text(encoding="utf-8"))
            postiz_enabled = (
                permissions["agents"]["publisher"].get("enabled") is True
                and permissions["connectors"]["postiz"].get("enabled") is True
            )
        except (OSError, UnicodeError, ValueError, KeyError, TypeError):
            postiz_enabled = False
        if not postiz_enabled:
            return result
        if publication_target is None:
            return {**result, "delivery_status": "needs_attention"}
        publish_mode = permissions.get("publish")
        scheduled_at = publication_target["scheduled_at"]
        operation = "draft"
        delivery_schedule = None
        if publish_mode == "automatic" and scheduled_at is not None:
            operation = "schedule" if scheduled_at > record["recorded_at"] else "now"
            delivery_schedule = scheduled_at if operation == "schedule" else None
        if self.broker_database is None or self.broker_permissions is None:
            return {**result, "delivery_status": "needs_attention"}
        try:
            from pipeline.publisher_worker import queue_publisher_task
            from pipeline.task_broker import TaskBroker

            broker = TaskBroker(self.broker_database, self.broker_permissions)
            try:
                publisher = queue_publisher_task(
                    broker, workspace=self.workspace,
                    profile_path=self._selected_profile(record["project_id"])[0],
                    parent_task_id=record["artifact_id"],
                    approval_id=record["action_id"],
                    release_package=packaged["release_package"],
                    platform=publication_target["platform"], operation=operation,
                    requested_at=record["recorded_at"],
                    scheduled_at=delivery_schedule,
                )
            finally:
                broker.close()
            if self.broker_automation is not None and publisher["status"] == "queued":
                self.broker_automation.start(
                    project=record["project_id"], task_id=publisher["task_id"],
                    profile_path=self._selected_profile(record["project_id"])[0],
                )
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
            return {**result, "delivery_status": "needs_attention"}
        delivery_status = {
            "queued": "queued", "running": "processing",
            "completed": "completed", "failed": "needs_attention",
            "cancelled": "needs_attention",
        }.get(publisher["status"], "needs_attention")
        return {
            **result, "delivery_status": delivery_status,
            "publisher_task_id": publisher["task_id"],
        }


class _ReviewHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: ReviewService,
                 static_root: Path | None = None,
                 remote_config: dict[str, Any] | None = None) -> None:
        self.review_service = service
        self.static_root = static_root
        self.remote_config = remote_config or {
            "enabled": False, "origin": None, "allowed_logins": []
        }
        super().__init__(address, ReviewRequestHandler)

    def server_close(self) -> None:
        self.review_service.close()
        super().server_close()


class ReviewRequestHandler(BaseHTTPRequestHandler):
    server: _ReviewHTTPServer
    server_version = "GrowthClockwork"
    sys_version = ""

    def version_string(self) -> str:
        return self.server_version

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: int, value: dict[str, Any]) -> None:
        content = _canonical_json(value) + b"\n"
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)
        except ConnectionError:
            # Navigation/React StrictMode can cancel an in-flight local read.
            # A disconnected client cannot receive a second error response.
            return

    def _error(self, error: ReviewError) -> None:
        self._send_json(
            error.status,
            {"error": {"code": error.code, "message": error.message}},
        )

    def _send_static(self, raw_path: str, *, head: bool = False) -> None:
        root = self.server.static_root
        if root is None:
            raise ReviewError(404, "not_found", "Endpoint not found")
        try:
            decoded = unquote_to_bytes(raw_path).decode("utf-8")
        except UnicodeError as exc:
            raise ReviewError(404, "not_found", "Page not found") from exc
        if (not decoded.startswith("/") or "\\" in decoded or "\x00" in decoded
                or any(part in {"", ".", ".."} for part in decoded.split("/")[1:] if part != "")):
            raise ReviewError(404, "not_found", "Page not found")
        relative = PurePosixPath(decoded.lstrip("/"))
        if decoded == "/" or not relative.name or "." not in relative.name:
            relative = PurePosixPath("index.html")
        path = root.joinpath(*relative.parts)
        try:
            if any(parent.is_symlink() for parent in [path, *path.parents] if parent != root.parent):
                raise OSError()
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= MAX_STATIC_BYTES:
                    raise OSError()
                with os.fdopen(descriptor, "rb", closefd=False) as source:
                    content = source.read(MAX_STATIC_BYTES + 1)
            finally:
                os.close(descriptor)
            if len(content) > MAX_STATIC_BYTES:
                raise OSError()
        except OSError as exc:
            raise ReviewError(404, "not_found", "Page not found") from exc
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if media_type.startswith("text/") or media_type in {"application/javascript", "application/json"}:
            media_type += "; charset=utf-8"
        try:
            self.send_response(200)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache" if relative.name == "index.html" else "public, max-age=31536000, immutable")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            if not head:
                self.wfile.write(content)
        except ConnectionError:
            return

    def do_HEAD(self) -> None:
        try:
            parsed = urlsplit(self.path)
            self._studio_local_origin()
            if parsed.path.startswith("/api/"):
                raise ReviewError(405, "method_not_allowed", "HEAD is not supported for API endpoints")
            self._send_static(parsed.path, head=True)
        except ReviewError as exc:
            self._error(exc)

    def _studio_local_origin(self) -> None:
        """Allow loopback, or one exact authenticated Tailscale Serve origin."""
        if self.client_address[0] != "127.0.0.1":
            raise ReviewError(403, "unsafe_origin", "Desk access requires the loopback proxy")
        host = self.headers.get("Host", "")
        try:
            parsed_host = urlsplit("http://" + host)
            explicit_port = parsed_host.port
        except ValueError as exc:
            raise ReviewError(403, "unsafe_origin", "Desk access requires a trusted host") from exc
        hostname = parsed_host.hostname
        if (hostname is None or parsed_host.username or parsed_host.path
                or parsed_host.query or parsed_host.fragment):
            raise ReviewError(403, "unsafe_origin", "Desk access requires a trusted host")
        local = hostname in {"localhost", "127.0.0.1"}
        config = self.server.remote_config
        remote = False
        expected_origin = None
        if config.get("enabled") is True:
            expected_origin = config.get("origin")
            expected_host = urlsplit(expected_origin).hostname
            remote = hostname == expected_host and explicit_port in {None, 443}
        if not local and not remote:
            raise ReviewError(403, "unsafe_origin", "Desk access requires a trusted host")
        if remote:
            login = self.headers.get("Tailscale-User-Login", "")
            allowed = config.get("allowed_logins", [])
            if not isinstance(login, str) or login.casefold() not in {
                    item.casefold() for item in allowed if isinstance(item, str)}:
                raise ReviewError(
                    403, "unsafe_identity",
                    "Desk access requires an allowed Tailscale identity",
                )
        origin = self.headers.get("Origin")
        if origin:
            try:
                parsed = urlsplit(origin)
                origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            except ValueError as exc:
                raise ReviewError(403, "unsafe_origin", "Desk access requires the same origin") from exc
            if local:
                host_port = explicit_port or 80
                accepted = (
                    parsed.scheme == "http"
                    and parsed.hostname in {"localhost", "127.0.0.1"}
                    and parsed.hostname == hostname
                    and origin_port == host_port
                )
            else:
                accepted = origin == expected_origin
            if (not accepted or parsed.username or parsed.password or parsed.path
                    or parsed.query or parsed.fragment):
                raise ReviewError(403, "unsafe_origin", "Desk access requires the same origin")

    def do_GET(self) -> None:
        try:
            parsed = urlsplit(self.path)
            self._studio_local_origin()
            if not parsed.path.startswith("/api/"):
                self._send_static(parsed.path)
                return
            query = parse_qs(parsed.query, keep_blank_values=True)
            if parsed.path == "/api/setup-status":
                if query:
                    raise ReviewError(400, "invalid_query", "Setup status accepts no query")
                self._send_json(200, self.server.review_service.setup_status())
                return
            if parsed.path == "/api/broker-status":
                if set(query) != {"project_id"} or len(query["project_id"]) != 1:
                    raise ReviewError(400, "invalid_query", "Broker status requires one project_id")
                self._send_json(200, self.server.review_service.broker_status(query["project_id"][0]))
                return
            if parsed.path == "/api/website-analytics":
                if set(query) != {"project_id"} or len(query["project_id"]) != 1:
                    raise ReviewError(400, "invalid_query", "Website analytics requires one project_id")
                project_id = query["project_id"][0]
                self.server.review_service._selected_profile(project_id)
                try:
                    report = website_analytics.read_report(
                        self.server.review_service.workspace, project_id
                    )
                except website_analytics.AnalyticsReportError as exc:
                    raise ReviewError(
                        409,
                        "invalid_analytics_report",
                        "The local analytics report could not be verified",
                    ) from exc
                report["setup"] = ga4_connector.readiness(
                    self.server.review_service.workspace, project_id
                )
                self._send_json(200, report)
                return
            if parsed.path == "/api/platform-analytics":
                if set(query) != {"project_id"} or len(query["project_id"]) != 1:
                    raise ReviewError(400, "invalid_query", "Platform analytics requires one project_id")
                project_id = query["project_id"][0]
                self.server.review_service._selected_profile(project_id)
                try:
                    report = website_analytics.read_platform_report(
                        self.server.review_service.workspace, project_id
                    )
                except website_analytics.AnalyticsReportError as exc:
                    raise ReviewError(
                        409,
                        "invalid_analytics_report",
                        "The local platform analytics report could not be verified",
                    ) from exc
                self._send_json(200, report)
                return
            if parsed.path == "/api/projects":
                if query:
                    raise ReviewError(400, "invalid_query", "Projects endpoint accepts no query")
                self._send_json(200, {"projects": self.server.review_service.projects()})
                return
            if parsed.path == "/api/studio":
                if set(query) != {"project_id"} or len(query["project_id"]) != 1:
                    raise ReviewError(400, "invalid_query", "Studio endpoint requires one project_id")
                _, profile = self.server.review_service._selected_profile(query["project_id"][0])
                self._send_json(200, studio.read(self.server.review_service.workspace, profile))
                return
            if parsed.path == "/api/reviews":
                if set(query) != {"project_id"} or len(query["project_id"]) != 1:
                    raise ReviewError(400, "invalid_query", "Reviews endpoint requires one project_id")
                self._send_json(200, self.server.review_service.reviews(query["project_id"][0]))
                return
            if parsed.path == "/api/project-desk":
                if set(query) != {"project_id"} or len(query["project_id"]) != 1:
                    raise ReviewError(
                        400,
                        "invalid_query",
                        "Project desk endpoint requires one project_id",
                    )
                self._send_json(
                    200,
                    self.server.review_service.project_desk(query["project_id"][0]),
                )
                return
            if parsed.path == "/api/research-feeds":
                if set(query) != {"project_id"} or len(query["project_id"]) != 1:
                    raise ReviewError(400, "invalid_query", "Research endpoint requires one project_id")
                self._send_json(200, self.server.review_service.research_feeds(query["project_id"][0]))
                return
            if parsed.path == "/api/goal-loop":
                if set(query) != {"project_id"} or len(query["project_id"]) != 1:
                    raise ReviewError(
                        400,
                        "invalid_query",
                        "Local check endpoint requires one project_id",
                    )
                self._send_json(200, self.server.review_service.goal_loop(query["project_id"][0]))
                return
            if parsed.path == "/api/evidence":
                if set(query) != {"project_id", "ref", "sha256"} or any(
                    len(query[key]) != 1 for key in ("project_id", "ref", "sha256")
                ):
                    raise ReviewError(
                        400,
                        "invalid_query",
                        "Evidence endpoint requires one project_id, ref, and sha256",
                    )
                self._send_json(
                    200,
                    self.server.review_service.evidence(
                        query["project_id"][0],
                        query["ref"][0],
                        query["sha256"][0],
                    ),
                )
                return
            if parsed.path == "/api/review-material":
                expected = {"project_id", "packet_ref", "packet_sha256", "material_id", "content_sha256"}
                if set(query) != expected or any(len(query[key]) != 1 for key in expected):
                    raise ReviewError(
                        400,
                        "invalid_query",
                        "Material endpoint requires one exact review packet and material identity",
                    )
                self._send_json(
                    200,
                    self.server.review_service.review_material(
                        query["project_id"][0], query["packet_ref"][0],
                        query["packet_sha256"][0], query["material_id"][0],
                        query["content_sha256"][0],
                    ),
                )
                return
            raise ReviewError(404, "not_found", "Endpoint not found")
        except ConnectionError:
            return
        except studio.StudioError as exc:
            self._error(ReviewError(exc.status, exc.code, str(exc)))
        except ReviewError as exc:
            self._error(exc)
        except Exception:
            self._error(ReviewError(500, "internal_error", "Internal review service error"))

    def do_POST(self) -> None:
        try:
            parsed = urlsplit(self.path)
            self._studio_local_origin()
            if parsed.path not in {"/api/review-actions", "/api/project-briefs", "/api/goal-loop", "/api/studio", "/api/materials", "/api/material-assets"} or parsed.query:
                raise ReviewError(404, "not_found", "Endpoint not found")
            if parsed.path == "/api/material-assets":
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/octet-stream":
                    raise ReviewError(415, "unsupported_media_type", "Material asset body must be application/octet-stream")
                if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Encoding"):
                    raise ReviewError(400, "invalid_request", "Encoded or chunked material uploads are not supported")
                raw_length = self.headers.get("Content-Length")
                try:
                    length = int(raw_length) if raw_length is not None else -1
                except ValueError as exc:
                    raise ReviewError(400, "invalid_request", "Content-Length is invalid") from exc
                if length < 1 or length > studio.MAX_UPLOAD_BYTES:
                    raise ReviewError(413, "invalid_request", "Material body must contain 1 byte to 64 MiB")

                def header(name: str, label: str, maximum: int) -> str:
                    raw = self.headers.get(name)
                    if raw is None or not raw or len(raw) > maximum * 4:
                        raise ReviewError(400, "invalid_input", f"{label} header is missing or too long")
                    try:
                        decoded = unquote_to_bytes(raw).decode("utf-8")
                    except (UnicodeError, ValueError) as exc:
                        raise ReviewError(400, "invalid_input", f"{label} header is invalid") from exc
                    if not decoded or len(decoded) > maximum or "\x00" in decoded or any(ord(char) < 32 for char in decoded):
                        raise ReviewError(400, "invalid_input", f"{label} header is invalid")
                    return decoded

                value = {
                    "project_id": header("X-Growth-Project-Id", "Project", 128),
                    "project_profile_revision": header("X-Growth-Profile-Revision", "Profile", 128),
                    "request_id": header("X-Growth-Request-Id", "Request", 36),
                    "name": header("X-Growth-Filename", "Filename", 180),
                    "mime_type": header("X-Growth-Mime-Type", "MIME type", 120),
                }
                profile_path, profile = self.server.review_service._selected_profile(value["project_id"])
                if value["project_profile_revision"] != profile["profile_revision"]:
                    raise ReviewError(409, "stale_revision", "The selected project profile changed; refresh before saving")
                content = self.rfile.read(length)
                if len(content) != length:
                    raise ReviewError(400, "invalid_request", "Material body ended before Content-Length")
                result = studio.write_binary(
                    self.server.review_service.workspace, profile_path, profile,
                    value, content,
                )
                self._send_json(201 if result["created"] else 200, result)
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                raise ReviewError(415, "unsupported_media_type", "Content-Type must be application/json")
            if self.headers.get("Transfer-Encoding"):
                raise ReviewError(400, "invalid_request", "Transfer-Encoding is not supported")
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length) if raw_length is not None else -1
            except ValueError as exc:
                raise ReviewError(400, "invalid_request", "Content-Length is invalid") from exc
            maximum = studio.MAX_UPLOAD_REQUEST_BYTES if parsed.path == "/api/materials" else MAX_REQUEST_BYTES
            if length < 1 or length > maximum:
                raise ReviewError(413, "invalid_request", "Request body size is invalid")
            content = self.rfile.read(length)
            if len(content) != length:
                raise ReviewError(400, "invalid_request", "Request body ended before Content-Length")
            value = _strict_request_json(content)
            if parsed.path in {"/api/studio", "/api/materials"}:
                profile_path, profile = self.server.review_service._selected_profile(value.get("project_id"))
                result = studio.write(self.server.review_service.workspace, profile_path, profile, value, material=parsed.path == "/api/materials")
                if parsed.path == "/api/studio":
                    result = self.server.review_service.admit_studio_proposal(
                        profile_path, profile, value, result)
                self._send_json(201 if result["created"] else 200, result)
                return
            if parsed.path == "/api/review-actions":
                record, replay = self.server.review_service.review_action(value)
                automation = self.server.review_service.finalize_review_action(record)
                self._send_json(
                    200 if replay else 201,
                    {"action_record": record, "idempotent_replay": replay,
                     "automation": automation},
                )
                return
            if parsed.path == "/api/goal-loop":
                self._send_json(202, self.server.review_service.start_goal_loop(value))
                return
            created = self.server.review_service.create_project_brief(value)
            self._send_json(
                201 if created["created"] else 200,
                {
                    "brief": {
                        "project_id": created["project_id"],
                        "project_profile_revision": created[
                            "project_profile_revision"
                        ],
                        "id": created["brief_id"],
                        "revision": created["brief_revision"],
                    },
                    "created": created["created"],
                    "desk": created["desk"],
                },
            )
        except ConnectionError:
            return
        except studio.StudioError as exc:
            self._error(ReviewError(exc.status, exc.code, str(exc)))
        except ReviewError as exc:
            self._error(exc)
        except Exception:
            self._error(ReviewError(500, "internal_error", "Internal review service error"))


def create_server(
    workspace: Path, *, port: int = 8765, host: str = "127.0.0.1",
    broker_database: Path | None = None,
    broker_permissions: Path | None = None,
    codex_executable: Path | None = None,
    static_root: Path | None = None,
) -> _ReviewHTTPServer:
    """Create a local-only HTTP server. Non-loopback binds are forbidden."""

    if host != "127.0.0.1":
        raise ReviewError(400, "unsafe_bind", "Review API may bind only to 127.0.0.1")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ReviewError(400, "invalid_port", "Port must be between 0 and 65535")
    checked_static_root = None
    if static_root is not None:
        workspace_root = Path(workspace).resolve()
        expected = workspace_root / "dashboard/dist"
        candidate = Path(static_root).resolve()
        if (candidate != expected or candidate.is_symlink() or not candidate.is_dir()
                or not (candidate / "index.html").is_file()
                or (candidate / "index.html").is_symlink()):
            raise ReviewError(400, "unsafe_static_root", "Static Desk root must be dashboard/dist")
        checked_static_root = candidate
    try:
        remote_config = remote_access.load(workspace)
    except remote_access.RemoteAccessError as exc:
        raise ReviewError(400, "invalid_remote_access", str(exc)) from exc
    return _ReviewHTTPServer((host, port), ReviewService(
        workspace, broker_database=broker_database,
        broker_permissions=broker_permissions,
        codex_executable=codex_executable), checked_static_root, remote_config)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--broker-database", type=Path, help="Optional existing broker DB")
    parser.add_argument("--broker-permissions", type=Path,
                        help="Optional local permission file for Studio task admission")
    parser.add_argument("--codex-executable", type=Path,
                        help="Optional absolute Codex CLI used by Marketing autostart")
    parser.add_argument("--static-root", type=Path,
                        help="Optional built dashboard at <workspace>/dashboard/dist")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import sys

    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        server = create_server(
            args.workspace, port=args.port, broker_database=args.broker_database,
            broker_permissions=args.broker_permissions,
            codex_executable=args.codex_executable, static_root=args.static_root)
    except ReviewError as exc:
        print(f"review-api: {exc.message}", file=sys.stderr)
        return 2
    try:
        print(f"review-api: http://127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
