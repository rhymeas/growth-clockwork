#!/usr/bin/env python3
"""Deterministic compiler for one Codex-authored Growth route plan.

Codex performs diagnosis and route selection. This module performs no reasoning,
model call, network request, shell command, publishing, or scheduling. It verifies
the selected profile, context, role/route registry, and contracts; snapshots exact
run inputs; materializes only the first executable DAG wave; and persists the
whole prepare transaction through Root Writer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Iterable
import uuid

from pipeline.agent_registry import (
    AgentRegistryError,
    RegistryArtifact,
    ResolvedAgentRegistry,
    RouteContract,
    load_agent_registry,
    route_for_activation_decisions,
)
from pipeline.context_resolver import (
    ContextResolutionError,
    DISCOVERY_CONTEXT_KINDS,
    ResolvedContext,
    resolve_project_context,
)
from pipeline import root_writer
from pipeline.operating_contract import (
    OperatingContractError,
    validate_operating_contract,
)
from pipeline.validate_json_suites import (
    SuiteConfigurationError,
    ValidationError,
    validate_registered_instance,
)


RUN_NAMESPACE = uuid.UUID("6d2fd6d0-80db-4ef8-89e5-227c102314b8")
WRITER_NAMESPACE = uuid.UUID("fc7d25ec-dab7-4b21-a540-5324be692fc3")
DISCOVERY_ROUTES = frozenset({"research-evidence", "content-channel"})
DISCOVERY_RESTRICTION = (
    "Discovery-only authority: draft or restricted context is non-authoritative "
    "research input, not approved product or market knowledge. Do not use blocked, "
    "missing, or retired context sections or their sources as evidence or claims. "
    "Do not make unapproved product claims. Audience assumptions remain explicitly "
    "labelled hypotheses; missing measurements remain unmeasured. Follow the exact "
    "current prohibited-claims and authority sections. Automated governance and "
    "human review of the exact final product remain required. No publication or "
    "external side effects."
)


class CoordinatorError(ValueError):
    """Raised before any work is dispatchable when compilation fails."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CoordinatorError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise CoordinatorError(f"{label} must be a JSON object")
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
        raise CoordinatorError(str(exc)) from exc
    if errors:
        raise CoordinatorError(
            f"{label} failed {schema_id}: {_format_errors(errors)}"
        )


def _pointer(ref: str, revision: str, content: bytes) -> dict[str, str]:
    return {
        "artifact_ref": ref,
        "artifact_revision": revision,
        "artifact_sha256": _sha256(content),
    }


def _write_item(path: str, content: bytes, media_type: str = "application/json") -> dict[str, Any]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CoordinatorError(f"Root Writer input is not UTF-8: {path}") from exc
    return {
        "path": path,
        "mode": "create",
        "content": text,
        "content_sha256": _sha256(content),
        "expected_sha256": None,
        "media_type": media_type,
    }


def _stable_identity(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            plan["project_id"],
            plan["project_profile_revision"],
            plan["request_id"],
        ]
    )


def _validate_plan_evidence(plan: dict[str, Any], context: ResolvedContext) -> None:
    allowed: set[tuple[str, str, str]] = set()
    for section in context.sections.values():
        if plan["run_mode"] == "discovery" and (
            section.value["usage_policy"] == "blocked"
            or section.value["material_state"] in {"missing", "retired"}
        ):
            continue
        allowed.add(
            (
                section.artifact.ref,
                section.artifact.revision,
                section.artifact.sha256,
            )
        )
        for source in section.sources:
            allowed.add((source.ref, source.revision, source.sha256))
    for evidence in plan["diagnosis"]["evidence_refs"]:
        identity = (
            evidence["artifact_ref"],
            evidence["artifact_revision"],
            evidence["artifact_sha256"],
        )
        if identity not in allowed:
            raise CoordinatorError(
                "coordinator-plan evidence is not an exact artifact in the selected context"
            )
    success_ref = plan["operating_contract"]["success_signal"]["source_ref"]
    if success_ref is not None:
        identity = (
            success_ref["artifact_ref"],
            success_ref["artifact_revision"],
            success_ref["artifact_sha256"],
        )
        if identity not in allowed:
            raise CoordinatorError(
                "operating-contract success source is not an exact artifact in the selected context"
            )


def _snapshot_context(
    profile_path: Path,
    context: ResolvedContext,
    run_id: str,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    base = f"records/run-inputs/{run_id}/context"
    writes: list[dict[str, Any]] = []
    source_paths: dict[tuple[str, str, str], str] = {}

    for section in context.sections.values():
        for source in section.sources:
            identity = (source.ref, source.revision, source.sha256)
            if identity in source_paths:
                continue
            suffix = Path(source.ref).suffix or ".json"
            snapshot_ref = f"{base}/sources/source-{source.sha256[:16]}{suffix}"
            source_paths[identity] = snapshot_ref
            writes.append(_write_item(snapshot_ref, source.content))

    manifest_sections: list[dict[str, str]] = []
    for kind in (
        "facts",
        "prohibited_claims",
        "audience",
        "market",
        "funnel",
        "metrics",
        "channels",
        "authority",
    ):
        resolved = context.sections[kind]
        section = json.loads(json.dumps(resolved.value))
        snapshot_revision = f"{section['section_revision']}-snapshot-{run_id[-8:]}"
        section["section_revision"] = snapshot_revision
        for source in section["source_refs"]:
            identity = (
                source["artifact_ref"],
                source["artifact_revision"],
                source["artifact_sha256"],
            )
            source["artifact_ref"] = source_paths[identity]
        _validate_instance(
            profile_path,
            "context-section-envelope@1",
            section,
            f"runtime context section {kind}",
        )
        section_content = _canonical_json(section)
        section_ref = f"{base}/sections/{kind}.json"
        writes.append(_write_item(section_ref, section_content))
        manifest_sections.append(
            {
                "kind": kind,
                "artifact_ref": section_ref,
                "artifact_revision": snapshot_revision,
                "artifact_sha256": _sha256(section_content),
            }
        )

    manifest = json.loads(json.dumps(context.value))
    snapshot_revision = f"{manifest['context_revision']}-snapshot-{run_id[-8:]}"
    manifest["context_revision"] = snapshot_revision
    manifest["sections"] = manifest_sections
    _validate_instance(
        profile_path,
        "project-context-manifest@1",
        manifest,
        "runtime project context manifest",
    )
    manifest_content = _canonical_json(manifest)
    manifest_ref = f"{base}/manifest.json"
    writes.append(_write_item(manifest_ref, manifest_content))
    return writes, _pointer(manifest_ref, snapshot_revision, manifest_content)


def _snapshot_contract(
    artifact: RegistryArtifact,
    snapshot_ref: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    return (
        _write_item(snapshot_ref, artifact.content),
        {
            "artifact_ref": snapshot_ref,
            "artifact_revision": artifact.revision,
            "artifact_sha256": artifact.sha256,
        },
    )


def _materialize_initial_tasks(
    profile_path: Path,
    profile: dict[str, Any],
    plan: dict[str, Any],
    route: RouteContract,
    registry: ResolvedAgentRegistry,
    run_id: str,
    plan_pointer: dict[str, str],
    context_pointer: dict[str, str],
    route_pointer: dict[str, str],
    role_pointers: dict[str, dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    task_ids = {
        node.node_id: f"{plan['work_item_id']}-T{index:03d}"
        for index, node in enumerate(route.nodes, start=1)
    }
    pattern = re.compile(profile["task_id_pattern"])
    for task_id in task_ids.values():
        if not pattern.fullmatch(task_id):
            raise CoordinatorError(
                f"generated task_id does not match the selected profile: {task_id}"
            )

    writes: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    for node in route.nodes:
        if node.depends_on:
            continue
        if len(tasks) >= route.max_concurrent_workhorses:
            break
        task_id = task_ids[node.node_id]
        task = {
            "project_id": plan["project_id"],
            "project_profile_revision": plan["project_profile_revision"],
            "task_version": "2.0",
            "task_id": task_id,
            "run_id": run_id,
            "step_id": node.node_id,
            "depends_on": [],
            "assigned_role": node.role_id,
            "role_contract": role_pointers[node.role_id],
            "route_id": route.route_id,
            "route_contract": route_pointer,
            "objective": f"{plan['objective']}\n\nAssigned step: {node.objective}",
            "project_context": context_pointer,
            "input_artifacts": [
                plan_pointer,
                context_pointer,
                route_pointer,
                role_pointers[node.role_id],
            ],
            "output_contract": node.output_contract,
            "authority": {
                "allowed_write_roots": list(node.allowed_write_roots),
                "external_side_effects": False,
                "may_publish": False,
            },
            "created_at": plan["requested_at"],
        }
        _validate_instance(profile_path, "task-envelope@2", task, f"task {task_id}")
        content = _canonical_json(task)
        task_ref = f"handoffs/{run_id}/{task_id}.json"
        writes.append(_write_item(task_ref, content))
        tasks.append(
            {
                "step_id": node.node_id,
                "task_id": task_id,
                "task_ref": task_ref,
                "task_sha256": _sha256(content),
                "status": "queued",
                "result_ref": None,
            }
        )
    if not tasks:
        raise CoordinatorError("selected route has no materializable initial task")
    return writes, tasks, task_ids


def prepare_run(
    workspace: Path,
    profile_path: Path,
    plan_path: Path,
) -> dict[str, Any]:
    """Compile and atomically persist the initial wave for one selected route."""

    try:
        workspace = root_writer._validate_workspace(workspace)
        profile = root_writer.load_project_profile(profile_path)
        root_writer._validate_profile_package(workspace, profile_path, profile)
    except root_writer.WriterError as exc:
        raise CoordinatorError(str(exc)) from exc
    plan = _json_object(plan_path, "coordinator plan")
    _validate_instance(profile_path, "coordinator-plan@1", plan, "coordinator plan")
    if plan["project_id"] != profile["project_id"]:
        raise CoordinatorError("coordinator plan belongs to another project")
    if plan["project_profile_revision"] != profile["profile_revision"]:
        raise CoordinatorError("coordinator plan uses a stale profile revision")
    if "_project_context" not in profile or "_agent_registry" not in profile:
        raise CoordinatorError("project profile has no pinned context or agent registry")
    if plan["project_context"] != profile["_project_context"]:
        raise CoordinatorError("coordinator plan does not use the profile-pinned context")
    try:
        parsed_request_id = uuid.UUID(plan["request_id"])
    except ValueError as exc:
        raise CoordinatorError("coordinator plan request_id is invalid") from exc
    if str(parsed_request_id) != plan["request_id"]:
        raise CoordinatorError("coordinator plan request_id is not canonical")

    try:
        registry = load_agent_registry(
            workspace, profile_path, profile["_agent_registry"]
        )
    except AgentRegistryError as exc:
        raise CoordinatorError(str(exc)) from exc
    route_id = plan["route_decision"]["selected_route_id"]
    if plan["run_mode"] == "discovery" and route_id not in DISCOVERY_ROUTES:
        raise CoordinatorError(
            "discovery mode supports only research-evidence and content-channel routes"
        )
    if route_id not in set(profile.get("enabled_routes", [])):
        raise CoordinatorError(f"route is not enabled by the profile: {route_id}")
    base_route = registry.routes.get(route_id)
    if base_route is None:
        raise CoordinatorError(f"unknown selected route: {route_id}")
    try:
        route = route_for_activation_decisions(
            base_route,
            plan["route_decision"],
            require_step_activation=True,
        )
    except AgentRegistryError as exc:
        raise CoordinatorError(str(exc)) from exc
    if "operating_contract" not in plan:
        raise CoordinatorError("new coordinator plans require an operating_contract")
    try:
        validate_operating_contract(
            profile_path,
            plan["operating_contract"],
            route_role_ids={node.role_id for node in route.nodes},
        )
    except OperatingContractError as exc:
        raise CoordinatorError(str(exc)) from exc
    if plan["operating_contract"]["created_at"] != plan["requested_at"]:
        raise CoordinatorError(
            "operating_contract.created_at must equal coordinator-plan requested_at"
        )
    if len(plan["diagnosis"]["evidence_refs"]) > plan["operating_contract"][
        "resource_limits"
    ]["max_sources"]:
        raise CoordinatorError("coordinator-plan evidence exceeds max_sources")
    if not any(
        root_writer._under_prefix(
            root_writer._validate_relative_path("memory/records", "memory destination"),
            allowed_root,
        )
        for allowed_root in profile["_allowed_roots"]
    ):
        raise CoordinatorError("project writer does not allow the memory destination")
    alternatives = plan["route_decision"]["alternatives"]
    if len(profile.get("enabled_routes", [])) > 1 and not alternatives:
        raise CoordinatorError("route decision must record at least one alternative")
    for alternative in alternatives:
        if alternative["route_id"] == route_id:
            raise CoordinatorError("selected route cannot also be an alternative")
        if alternative["route_id"] not in registry.routes:
            raise CoordinatorError(
                f"route decision names an unknown alternative: {alternative['route_id']}"
            )

    try:
        context = resolve_project_context(
            profile_path,
            plan["project_context"],
            run_mode=plan["run_mode"],
            required_kinds=(
                DISCOVERY_CONTEXT_KINDS
                if plan["run_mode"] == "discovery"
                else route.context_requirements
            ),
        )
    except ContextResolutionError as exc:
        raise CoordinatorError(str(exc)) from exc
    _validate_plan_evidence(plan, context)
    if plan["run_mode"] == "discovery":
        # Persist the boundary in the plan and manifest so every later DAG wave,
        # not just the initially dispatched role, inherits the same instructions.
        plan["objective"] += f"\n\n{DISCOVERY_RESTRICTION}"

    stable_identity = _stable_identity(plan)
    run_uuid = uuid.uuid5(RUN_NAMESPACE, stable_identity)
    run_id = f"RUN-{run_uuid.hex[:16]}"
    writer_key = str(uuid.uuid5(WRITER_NAMESPACE, stable_identity))
    plan_content = _canonical_json(plan)
    plan_ref = f"records/coordinator-plans/{plan['request_id']}.json"
    plan_pointer = _pointer(plan_ref, plan["request_id"], plan_content)
    writes: list[dict[str, Any]] = [_write_item(plan_ref, plan_content)]

    context_writes, runtime_context_pointer = _snapshot_context(
        profile_path, context, run_id
    )
    writes.extend(context_writes)
    registry_ref = f"records/run-inputs/{run_id}/agents/registry.json"
    registry_write, _ = _snapshot_contract(registry.artifact, registry_ref)
    writes.append(registry_write)
    route_ref = f"records/run-inputs/{run_id}/agents/route.json"
    route_write, route_pointer = _snapshot_contract(route.artifact, route_ref)
    writes.append(route_write)

    role_pointers: dict[str, dict[str, str]] = {}
    for role_id in sorted({node.role_id for node in route.nodes}):
        role = registry.roles[role_id]
        role_ref = f"records/run-inputs/{run_id}/agents/roles/{role_id}.json"
        role_write, role_pointer = _snapshot_contract(role.artifact, role_ref)
        writes.append(role_write)
        role_pointers[role_id] = role_pointer

    task_writes, tasks, task_ids = _materialize_initial_tasks(
        profile_path,
        profile,
        plan,
        route,
        registry,
        run_id,
        plan_pointer,
        runtime_context_pointer,
        route_pointer,
        role_pointers,
    )
    writes.extend(task_writes)
    manifest = {
        "project_id": plan["project_id"],
        "project_profile_revision": plan["project_profile_revision"],
        "manifest_version": "2.0",
        "manifest_revision": 1,
        "previous_manifest": None,
        "run_id": run_id,
        "run_mode": plan["run_mode"],
        "status": "queued",
        "objective": plan["objective"],
        "coordinator_plan": plan_pointer,
        "project_context": runtime_context_pointer,
        "route_id": route_id,
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
        "started_at": plan["requested_at"],
        "completed_at": None,
    }
    _validate_instance(profile_path, "run-manifest@2", manifest, "run manifest")
    manifest_content = _canonical_json(manifest)
    manifest_ref = f"records/runs/{run_id}/manifest-r0001.json"
    writes.append(_write_item(manifest_ref, manifest_content))

    request = {
        "writer_request_version": "1.0",
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "run_id": run_id,
        "idempotency_key": writer_key,
        "requested_by": profile["root_role_id"],
        "writes": writes,
    }
    request_content = _canonical_json(request)
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix="growth-coordinator-", suffix=".json", delete=False
        ) as handle:
            handle.write(request_content)
            request_path = Path(handle.name)
        try:
            writer_receipt = root_writer.apply_request(
                workspace, profile_path, request_path
            )
        finally:
            request_path.unlink(missing_ok=True)
    except (OSError, root_writer.WriterError) as exc:
        raise CoordinatorError(str(exc)) from exc

    expected_paths = {item["path"] for item in writes}
    receipt_paths = {item["path"] for item in writer_receipt["writes"]}
    if receipt_paths != expected_paths or manifest_ref not in receipt_paths:
        raise CoordinatorError("completed writer receipt does not cover the compiled run")
    return {
        "status": "prepared",
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "run_id": run_id,
        "route_id": route_id,
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
        "writer_receipt": writer_receipt,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    prepare = subcommands.add_parser("prepare", help="compile one initial route wave")
    prepare.add_argument("--workspace", required=True, type=Path)
    prepare.add_argument("--project-config", required=True, type=Path)
    prepare.add_argument("--plan", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_run(args.workspace, args.project_config, args.plan)
        else:  # pragma: no cover - argparse prevents this branch.
            raise CoordinatorError(f"unsupported command: {args.command}")
    except CoordinatorError as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
