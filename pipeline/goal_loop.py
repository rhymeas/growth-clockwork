"""Small durable local goal loop for the Project Desk.

The loop deliberately performs no network request, model call, shell command or
public action.  It turns one already-recorded operator brief into a durable local
preflight result.  That result is useful because it verifies the exact project
context pointer and states which inputs are still missing; it is not presented as
market research, a content draft, or a publication decision.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import tempfile
import threading
from typing import Any
import uuid

from pipeline import root_writer
from pipeline.project_start import (
    ProjectStartError,
    ProjectStartService,
    _choose_brief,
    _load_briefs,
)


GOAL_LOOP_VERSION = "1.0"
GOAL_LOOP_NAMESPACE = uuid.UUID("c2e2f7a9-3dbf-4574-8b35-6c7f18a3df5b")
GOAL_ID_PREFIX = "GOAL-"


class GoalLoopError(ValueError):
    """A safe, API-facing local goal-loop failure."""

    def __init__(
        self, message: str, *, status: int = 409, code: str = "goal_loop_unavailable"
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _canonical_json(value: Any) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
    except (TypeError, UnicodeEncodeError) as exc:
        raise GoalLoopError("The local goal result could not be recorded") from exc


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _strict_object(content: bytes, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate key {key}")
            value[key] = item
        return value

    try:
        value = json.loads(content.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise GoalLoopError(f"The {label} is not valid state") from exc
    if not isinstance(value, dict):
        raise GoalLoopError(f"The {label} is not valid state")
    return value


def _exact_keys(value: Any, required: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != required:
        raise GoalLoopError(f"The {label} is incomplete", status=400, code="invalid_goal_loop")
    return value


def _job_id(profile: dict[str, Any], brief: dict[str, str]) -> str:
    identity = "\n".join(
        (
            GOAL_LOOP_VERSION,
            profile["project_id"],
            profile["profile_revision"],
            brief["artifact_ref"],
            brief["artifact_revision"],
            brief["artifact_sha256"],
        )
    )
    return f"{GOAL_ID_PREFIX}{_sha256(identity.encode('utf-8'))[:24]}"


def _request_path(job_id: str) -> PurePosixPath:
    return PurePosixPath("records") / "goal-loops" / job_id / "request-r1.json"


def _result_path(job_id: str) -> PurePosixPath:
    return PurePosixPath("records") / "goal-loops" / job_id / "result-r1.json"


class GoalLoopService:
    """Queue and complete one deterministic local preflight per exact brief."""

    def __init__(self, workspace: Path) -> None:
        try:
            self.workspace = root_writer._validate_workspace(workspace)
        except root_writer.WriterError as exc:
            raise GoalLoopError("The local workspace is unavailable", status=500) from exc
        self._project_start = ProjectStartService(self.workspace)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="growth-goal")
        self._futures: dict[str, Future[None]] = {}
        self._futures_lock = threading.Lock()

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)

    def _selected(self, project_id: Any) -> tuple[Path, dict[str, Any]]:
        try:
            return self._project_start._selected_profile(project_id)
        except ProjectStartError as exc:
            raise GoalLoopError(
                "The selected project is unavailable.", status=exc.status, code=exc.code
            ) from exc

    def _current_brief(
        self, profile_path: Path, profile: dict[str, Any]
    ) -> tuple[dict[str, str], dict[str, Any]]:
        with root_writer._workspace_lock(self.workspace):
            selected = _choose_brief(_load_briefs(profile_path), None)
        if selected is None:
            raise GoalLoopError(
                "Set a project brief before starting the local check.",
                status=409,
                code="brief_required",
            )
        return selected

    @staticmethod
    def _context_summary(profile_path: Path, profile: dict[str, Any]) -> dict[str, str]:
        pointer = profile.get("project_context")
        if not isinstance(pointer, dict) or set(pointer) != {
            "artifact_ref",
            "artifact_revision",
            "artifact_sha256",
        }:
            return {"state": "missing", "label": "Project context"}
        ref = pointer["artifact_ref"]
        revision = pointer["artifact_revision"]
        expected_hash = pointer["artifact_sha256"]
        if (
            not isinstance(ref, str)
            or not ref.startswith("context/manifests/")
            or not isinstance(revision, str)
            or not isinstance(expected_hash, str)
            or len(expected_hash) != 64
        ):
            raise GoalLoopError("The pinned project context is unavailable")
        relative = PurePosixPath(ref)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise GoalLoopError("The pinned project context is unavailable")
        current = profile_path.parent
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise GoalLoopError("The pinned project context is unavailable")
        try:
            content = current.read_bytes()
        except OSError as exc:
            raise GoalLoopError("The pinned project context is unavailable") from exc
        if _sha256(content) != expected_hash:
            raise GoalLoopError("The pinned project context changed. Refresh the project first.")
        manifest = _strict_object(content, "project context")
        if (
            manifest.get("project_id") != profile["project_id"]
            or manifest.get("project_profile_revision") != profile["profile_revision"]
            or not isinstance(manifest.get("status"), str)
        ):
            raise GoalLoopError("The pinned project context is unavailable")
        return {
            "state": manifest["status"],
            "label": "Pinned project context",
            "revision": revision,
        }

    @staticmethod
    def _request_record(
        profile: dict[str, Any], job_id: str, brief: dict[str, str], context: dict[str, str]
    ) -> dict[str, Any]:
        return {
            "goal_loop_version": GOAL_LOOP_VERSION,
            "job_id": job_id,
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
            "brief": dict(brief),
            "context": dict(context),
            "execution": {
                "mode": "local_preflight",
                "network": False,
                "model": False,
                "public_action": False,
            },
        }

    @staticmethod
    def _result_record(request: dict[str, Any], brief: dict[str, Any]) -> dict[str, Any]:
        context_state = request["context"]["state"]
        context_detail = (
            "The pinned context is marked ready for its configured use."
            if context_state == "ready"
            else "The pinned context is not ready for autonomous research and is kept as a planning input only."
        )
        return {
            "goal_loop_result_version": GOAL_LOOP_VERSION,
            "job_id": request["job_id"],
            "project_id": request["project_id"],
            "project_profile_revision": request["project_profile_revision"],
            "brief": request["brief"],
            "status": "completed",
            "outcome": "needs_input",
            "title": "Local starting check complete",
            "summary": "The goal and its pinned context were checked locally. No web research, model call, content draft, or public action was performed.",
            "next_step": brief["research_question"],
            "insights": [
                {
                    "label": "Project context",
                    "state": context_state,
                    "detail": context_detail,
                },
                {
                    "label": "External evidence",
                    "state": "not_run",
                    "detail": "No external source was collected in this local check.",
                },
                {
                    "label": "Public actions",
                    "state": "protected",
                    "detail": "This check cannot publish, post, upload, merge, or change an account.",
                },
            ],
            "sources": [
                {"label": "Operator project brief", "state": "recorded"},
                {
                    "label": request["context"]["label"],
                    "state": context_state,
                },
            ],
            "limits": [
                "This is a local planning check, not audience validation or market research.",
                "It uses no model provider, analytics property, or publishing connection.",
            ],
        }

    def _read_state_json(
        self, profile: dict[str, Any], relative: PurePosixPath
    ) -> dict[str, Any] | None:
        try:
            with root_writer._project_state_fd(self.workspace, profile["_state_root"]) as state_fd:
                content = root_writer._read_relative_bytes(state_fd, relative, missing_ok=True)
        except root_writer.WriterError as exc:
            raise GoalLoopError("The local goal state is unavailable") from exc
        if content is None:
            return None
        return _strict_object(content, "local goal state")

    def _write_record(
        self,
        profile_path: Path,
        profile: dict[str, Any],
        job_id: str,
        kind: str,
        relative: PurePosixPath,
        value: dict[str, Any],
    ) -> None:
        content = _canonical_json(value)
        request = {
            "writer_request_version": root_writer.REQUEST_VERSION,
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
            "run_id": job_id,
            "idempotency_key": str(uuid.uuid5(GOAL_LOOP_NAMESPACE, f"{kind}\n{job_id}")),
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
        with tempfile.TemporaryDirectory(prefix="growth-goal-loop-") as temporary:
            request_path = Path(temporary) / "request.json"
            request_path.write_bytes(_canonical_json(request))
            try:
                root_writer.apply_request(self.workspace, profile_path, request_path)
            except root_writer.WriterError as exc:
                raise GoalLoopError("The local goal result could not be recorded") from exc

    def _schedule(
        self,
        profile_path: Path,
        profile: dict[str, Any],
        job_id: str,
        brief: dict[str, Any],
        request: dict[str, Any],
    ) -> None:
        with self._futures_lock:
            existing = self._futures.get(job_id)
            if existing is not None and not existing.done():
                return
            self._futures[job_id] = self._executor.submit(
                self._complete, profile_path, profile, job_id, brief, request
            )

    def _complete(
        self,
        profile_path: Path,
        profile: dict[str, Any],
        job_id: str,
        brief: dict[str, Any],
        request: dict[str, Any],
    ) -> None:
        if self._read_state_json(profile, _result_path(job_id)) is not None:
            return
        result = self._result_record(request, brief)
        self._write_record(profile_path, profile, job_id, "result", _result_path(job_id), result)

    def _job_response(
        self,
        profile: dict[str, Any],
        job_id: str,
        request: dict[str, Any] | None,
        result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
            "job": {
                "id": job_id,
                "status": "completed" if result is not None else "queued" if request is not None else "not_started",
                "request_recorded": request is not None,
                "result": self._public_result(result) if result is not None else None,
            },
        }

    @staticmethod
    def _public_result(value: dict[str, Any]) -> dict[str, Any]:
        required = {
            "status",
            "outcome",
            "title",
            "summary",
            "next_step",
            "insights",
            "sources",
            "limits",
        }
        if not required.issubset(value):
            raise GoalLoopError("The local goal result is invalid")
        return {key: value[key] for key in required}

    def status(self, project_id: Any) -> dict[str, Any]:
        profile_path, profile = self._selected(project_id)
        try:
            brief_pointer, brief = self._current_brief(profile_path, profile)
        except GoalLoopError as exc:
            if exc.code != "brief_required":
                raise
            return {
                "project_id": profile["project_id"],
                "project_profile_revision": profile["profile_revision"],
                "job": {"id": None, "status": "not_started", "request_recorded": False, "result": None},
            }
        job_id = _job_id(profile, brief_pointer)
        context = self._context_summary(profile_path, profile)
        expected_request = self._request_record(profile, job_id, brief_pointer, context)
        request = self._read_state_json(profile, _request_path(job_id))
        result = self._read_state_json(profile, _result_path(job_id))
        if request is not None and request != expected_request:
            raise GoalLoopError("The local goal request is invalid")
        if request is not None and result is None:
            # A request survives a desk/API restart. A later safe status read
            # resumes it from its exact immutable brief and pinned context.
            self._schedule(profile_path, profile, job_id, brief, request)
        return self._job_response(profile, job_id, request, result)

    def start(self, payload: Any) -> dict[str, Any]:
        value = _exact_keys(
            payload,
            {"project_id", "project_profile_revision"},
            "local goal request",
        )
        profile_path, profile = self._selected(value["project_id"])
        if value["project_profile_revision"] != profile["profile_revision"]:
            raise GoalLoopError(
                "This project changed. Refresh the desk before starting the local check.",
                status=409,
                code="stale_project_profile",
            )
        brief_pointer, brief = self._current_brief(profile_path, profile)
        context = self._context_summary(profile_path, profile)
        job_id = _job_id(profile, brief_pointer)
        request = self._read_state_json(profile, _request_path(job_id))
        result = self._read_state_json(profile, _result_path(job_id))
        expected_request = self._request_record(profile, job_id, brief_pointer, context)
        if request is not None and request != expected_request:
            raise GoalLoopError("The local goal request is invalid")
        if result is not None:
            return self._job_response(profile, job_id, request, result)
        if request is None:
            request = expected_request
            self._write_record(profile_path, profile, job_id, "request", _request_path(job_id), request)
        self._schedule(profile_path, profile, job_id, brief, request)
        return self._job_response(profile, job_id, request, None)
