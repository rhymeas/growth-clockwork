#!/usr/bin/env python3
"""Package authored Codex work and submit it through the existing Run Engine.

This boundary computes identities and exact-byte hashes. It does not draft
payloads, infer successful checks, call models, or write project state itself.
Pointers may use {"local_id": "name"} for a supporting artifact or an earlier
output in this bundle. Supporting content is an exact UTF-8 string.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
from typing import Any

from pipeline import run_engine
from pipeline.professional_contracts import ProfessionalContractError, _json_object, _pointer


class WorkhorseError(ValueError):
    """An authored bundle cannot be packaged for its selected task."""


BUNDLE_FIELDS = {
    "bundle_version", "task_ref", "status", "outputs", "supporting_artifacts",
    "evidence_refs", "checks", "attempts", "usage", "provenance", "created_at",
    "error",
}
OUTPUT_FIELDS = {
    "local_id", "contract_id", "artifact_ref", "artifact_revision", "payload",
    "evidence_refs", "unknowns", "assumptions", "confidence", "quality_checks",
}
SUPPORT_FIELDS = {
    "local_id", "artifact_ref", "artifact_revision", "media_type", "content",
}


def _object(value: Any, required: set[str], optional: set[str], label: str) -> dict[str, Any]:
    try:
        return run_engine._require_exact_keys(value, required, optional, label)
    except run_engine.RunEngineError as exc:
        raise WorkhorseError(str(exc)) from exc


def _read_bundle(path: Path) -> dict[str, Any]:
    try:
        return _json_object(path.read_bytes(), "workhorse bundle")
    except (OSError, ProfessionalContractError) as exc:
        raise WorkhorseError(str(exc)) from exc


def _resolve(value: Any, pointers: dict[str, dict[str, str]]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"local_id"}:
            local_id = value["local_id"]
            if not isinstance(local_id, str) or local_id not in pointers:
                raise WorkhorseError(f"unresolved or forward local_id: {local_id!r}")
            return dict(pointers[local_id])
        return {key: _resolve(item, pointers) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item, pointers) for item in value]
    return value


def prepare_submission(
    workspace: Path,
    profile_path: Path,
    task_ref: str,
    bundle: dict[str, Any],
) -> dict[str, Any]:
    """Return a deterministic submission without changing runtime state.

    The Run Engine remains the authority for professional validation, output
    ownership, evidence receipts, task completion and final-review lineage.
    """
    authored = copy.deepcopy(_object(bundle, BUNDLE_FIELDS, {"review_candidate"}, "bundle"))
    if authored["bundle_version"] != "1.0":
        raise WorkhorseError("unsupported workhorse bundle_version")
    if authored["task_ref"] != task_ref:
        raise WorkhorseError("bundle task_ref differs from selected task_ref")
    provenance = _object(authored["provenance"], {"prompt_version"}, {"model"}, "provenance")
    for field, value in provenance.items():
        if not isinstance(value, str) or not value.strip():
            raise WorkhorseError(f"provenance.{field} must be explicit non-empty text")
    _object(
        authored["usage"],
        {"duration_ms", "input_tokens", "output_tokens", "variable_external_cost_eur"},
        set(), "usage",
    )
    for field in ("outputs", "supporting_artifacts", "evidence_refs", "checks"):
        if not isinstance(authored[field], list):
            raise WorkhorseError(f"bundle.{field} must be an array")

    workspace = Path(workspace).resolve()
    profile_path = Path(profile_path)
    if not profile_path.is_absolute():
        profile_path = workspace / profile_path
    workspace, profile = run_engine._load_profile(workspace, profile_path)
    task_content = run_engine._state_bytes(workspace, profile, task_ref)
    assert task_content is not None
    task_hint = run_engine._json_object_bytes(task_content, "workhorse task")
    run_id = task_hint.get("run_id")
    if not isinstance(run_id, str) or not run_engine.root_writer.RUN_ID_RE.fullmatch(run_id):
        raise WorkhorseError("task has no valid run_id")
    manifest, _, _ = run_engine._latest_manifest(workspace, profile_path, profile, run_id)
    entries = [item for item in manifest["tasks"] if item["task_ref"] == task_ref]
    if len(entries) != 1:
        raise WorkhorseError("selected task_ref is not one exact materialized run task")
    task, task_content = run_engine._load_task(
        workspace, profile_path, profile, manifest, entries[0]
    )
    identity = {
        "project_id": task["project_id"],
        "project_profile_revision": task["project_profile_revision"],
        "run_id": task["run_id"],
        "task_id": task["task_id"],
        "task_sha256": run_engine._sha256(task_content),
        "step_id": task["step_id"],
        "role_id": task["assigned_role"],
    }
    pointers: dict[str, dict[str, str]] = {}
    refs: set[str] = set()
    artifacts: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []

    def materialize(item: dict[str, Any], content: str, media_type: str) -> dict[str, str]:
        local_id = item["local_id"]
        if not isinstance(local_id, str) or not local_id.strip() or local_id in pointers:
            raise WorkhorseError("each artifact needs a unique, non-empty local_id")
        if not isinstance(content, str) or not isinstance(media_type, str) or not media_type.strip():
            raise WorkhorseError("artifact content must be UTF-8 text with an explicit media_type")
        try:
            data = content.encode("utf-8")
            run_engine.root_writer._validate_relative_path(item["artifact_ref"], "artifact_ref")
        except (UnicodeEncodeError, run_engine.root_writer.WriterError) as exc:
            raise WorkhorseError(str(exc)) from exc
        pointer = run_engine._pointer(item["artifact_ref"], item["artifact_revision"], data)
        try:
            _pointer(pointer, "bundle artifact")
        except ProfessionalContractError as exc:
            raise WorkhorseError(str(exc)) from exc
        if pointer["artifact_ref"] in refs:
            raise WorkhorseError("bundle reuses an artifact_ref")
        refs.add(pointer["artifact_ref"])
        pointers[local_id] = pointer
        artifacts.append({**pointer, "media_type": media_type, "content": content})
        return pointer

    for item in authored["supporting_artifacts"]:
        item = _object(item, SUPPORT_FIELDS, set(), "supporting artifact")
        materialize(item, item["content"], item["media_type"])
    for item in authored["outputs"]:
        item = _object(item, OUTPUT_FIELDS, set(), "output")
        value = {
            "deliverable_version": "1.0",
            **identity,
            **{
                key: _resolve(item[key], pointers)
                for key in (
                    "contract_id", "artifact_revision", "payload", "evidence_refs",
                    "unknowns", "assumptions", "confidence", "quality_checks",
                )
            },
            "created_at": authored["created_at"],
        }
        content = run_engine._canonical_json(value).decode("utf-8")
        pointer = materialize(item, content, "application/json")
        outputs.append({**pointer, "contract_id": item["contract_id"]})

    result = {
        **identity,
        "result_version": "2.0",
        "status": authored["status"],
        "output_artifacts": outputs,
        "evidence_artifacts": _resolve(authored["evidence_refs"], pointers),
        "checks": _resolve(authored["checks"], pointers),
        "attempts": authored["attempts"],
        "usage": authored["usage"],
        "provenance": {
            "provider": "codex",
            "model": provenance.get("model", "not_exposed"),
            "prompt_version": provenance["prompt_version"],
            "contract_version": "result-envelope@2",
            "input_revision": identity["task_sha256"],
            "output_revision": outputs[0]["artifact_revision"] if outputs else None,
        },
        "created_at": authored["created_at"],
        "error": authored["error"],
    }
    run_engine._validate_instance(profile_path, "result-envelope@2", result, "workhorse result")
    submission = {
        "submission_version": "1.0",
        **{key: identity[key] for key in ("project_id", "project_profile_revision", "run_id", "task_id")},
        "result": result,
        "artifacts": artifacts,
    }
    if "review_candidate" in authored:
        candidate = _object(
            authored["review_candidate"],
            {"artifact", "artifact_id", "title", "type", "preview", "evidence", "quality_checks"},
            set(), "review_candidate",
        )
        resolved = _resolve(candidate, pointers)
        pointer = resolved.pop("artifact")
        try:
            pointer = _pointer(pointer, "review artifact")
        except ProfessionalContractError as exc:
            raise WorkhorseError(str(exc)) from exc
        submission["review_candidate"] = {**resolved, **pointer}
    return submission


def submit_bundle(
    workspace: Path, profile_path: Path, task_ref: str, bundle_path: Path
) -> dict[str, Any]:
    """Delegate every runtime write to accept_result and its Root Writer."""
    workspace = Path(workspace).resolve()
    profile_path = Path(profile_path)
    if not profile_path.is_absolute():
        profile_path = workspace / profile_path
    submission = prepare_submission(workspace, profile_path, task_ref, _read_bundle(bundle_path))
    with tempfile.TemporaryDirectory(prefix="growth-workhorse-") as temporary:
        input_path = Path(temporary) / "submission.json"
        input_path.write_bytes(run_engine._canonical_json(submission))
        return run_engine.accept_result(workspace, profile_path, input_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "submit"))
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--project-config", required=True, type=Path)
    parser.add_argument("--task-ref", required=True)
    parser.add_argument("--bundle", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            response = prepare_submission(
                args.workspace, args.project_config, args.task_ref, _read_bundle(args.bundle)
            )
        else:
            response = submit_bundle(args.workspace, args.project_config, args.task_ref, args.bundle)
    except (WorkhorseError, run_engine.RunEngineError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(response, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
