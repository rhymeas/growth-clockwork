#!/usr/bin/env python3
"""Load and semantically validate the portable Growth role/route registry."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

from pipeline import root_writer
from pipeline.validate_json_suites import SuiteConfigurationError, load_registered_schema


class AgentRegistryError(ValueError):
    """Raised when a registry, role, route, or profile binding is unsafe."""


@dataclass(frozen=True)
class RegistryArtifact:
    ref: str
    revision: str
    sha256: str
    path: Path
    content: bytes
    value: dict[str, Any]


@dataclass(frozen=True)
class RoleContract:
    role_id: str
    artifact: RegistryArtifact
    output_contracts: frozenset[str]


@dataclass(frozen=True)
class RouteNode:
    node_id: str
    role_id: str
    depends_on: tuple[str, ...]
    objective: str
    output_contract: str
    deliverable_contract: str
    allowed_write_roots: tuple[str, ...]
    optional: bool
    run_when: str


@dataclass(frozen=True)
class RouteContract:
    route_id: str
    artifact: RegistryArtifact
    context_requirements: tuple[str, ...]
    nodes: tuple[RouteNode, ...]
    final_product_contract: str
    final_product_node: str
    governance_node: str
    max_concurrent_workhorses: int
    only_independent_nodes: bool


@dataclass(frozen=True)
class ResolvedAgentRegistry:
    artifact: RegistryArtifact
    roles: dict[str, RoleContract]
    routes: dict[str, RouteContract]


def route_with_active_nodes(
    route: RouteContract,
    active_node_ids: set[str],
) -> RouteContract:
    """Return one legal route projection with skipped dependencies removed."""

    all_ids = {node.node_id for node in route.nodes}
    unknown = sorted(active_node_ids - all_ids)
    if unknown:
        raise AgentRegistryError(
            "route activation names unknown step(s): " + ", ".join(unknown)
        )
    required = {node.node_id for node in route.nodes if not node.optional}
    missing = sorted(required - active_node_ids)
    if missing:
        raise AgentRegistryError(
            "route activation cannot skip non-optional step(s): " + ", ".join(missing)
        )
    if route.final_product_node not in active_node_ids:
        raise AgentRegistryError("route activation cannot skip the final-product step")
    if route.governance_node not in active_node_ids:
        raise AgentRegistryError("route activation cannot skip the Governance step")

    nodes = tuple(
        replace(
            node,
            depends_on=tuple(
                dependency
                for dependency in node.depends_on
                if dependency in active_node_ids
            ),
        )
        for node in route.nodes
        if node.node_id in active_node_ids
    )
    _check_acyclic(nodes, route.route_id)
    return replace(route, nodes=nodes)


def route_for_activation_decisions(
    route: RouteContract,
    route_decision: Any,
    *,
    require_step_activation: bool,
) -> RouteContract:
    """Resolve optional steps from a plan, retaining legacy all-active behavior."""

    decision = _object(route_decision, "route decision")
    raw = decision.get("step_activation")
    if raw is None:
        if require_step_activation:
            raise AgentRegistryError(
                "route decision must define step_activation for every optional step"
            )
        return route
    if not isinstance(raw, list):
        raise AgentRegistryError("route decision step_activation must be an array")

    optional_ids = {node.node_id for node in route.nodes if node.optional}
    decisions: dict[str, str] = {}
    for index, value in enumerate(raw):
        item = _object(value, f"route decision step_activation[{index}]")
        if set(item) != {"step_id", "decision", "reason"}:
            raise AgentRegistryError(
                "route decision step_activation entries must contain only "
                "step_id, decision, and reason"
            )
        step_id = _portable(item.get("step_id"), "step activation step_id")
        if step_id in decisions:
            raise AgentRegistryError(f"duplicate step activation decision: {step_id}")
        if step_id not in optional_ids:
            raise AgentRegistryError(
                f"step activation may name only an optional route step: {step_id}"
            )
        action = item.get("decision")
        if action not in {"activate", "skip"}:
            raise AgentRegistryError(
                f"step activation decision is invalid for {step_id}"
            )
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise AgentRegistryError(
                f"step activation reason must be non-empty for {step_id}"
            )
        decisions[step_id] = action

    missing = sorted(optional_ids - decisions.keys())
    if missing:
        raise AgentRegistryError(
            "route decision is missing optional step activation(s): "
            + ", ".join(missing)
        )
    active_ids = {
        node.node_id
        for node in route.nodes
        if not node.optional or decisions[node.node_id] == "activate"
    }
    return route_with_active_nodes(route, active_ids)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AgentRegistryError(f"{label} must be a JSON object")
    return value


def _json_object(content: bytes, label: str) -> dict[str, Any]:
    try:
        return _object(json.loads(content.decode("utf-8")), label)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AgentRegistryError(f"{label} is not valid UTF-8 JSON: {exc}") from exc


def _portable(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or not root_writer.RUN_ID_RE.fullmatch(value)
    ):
        raise AgentRegistryError(f"{label} is not a portable identifier")
    return value


def _contract(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[a-z][a-z0-9-]+@[0-9]+", value
    ):
        raise AgentRegistryError(f"{label} is not a versioned contract identifier")
    return value


def _resolve_regular(root: Path, raw_ref: Any, label: str) -> Path:
    if not isinstance(raw_ref, str) or not raw_ref:
        raise AgentRegistryError(f"{label} must be a non-empty relative path")
    if "\\" in raw_ref or "\x00" in raw_ref:
        raise AgentRegistryError(f"{label} contains forbidden characters")
    relative = Path(raw_ref)
    if relative.is_absolute() or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise AgentRegistryError(f"{label} escapes its allowed root")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise AgentRegistryError(f"{label} may not traverse a symlink")
    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise AgentRegistryError(f"{label} is missing or escapes its allowed root") from exc
    if not resolved.is_file():
        raise AgentRegistryError(f"{label} is not a regular file")
    return resolved


def _artifact(
    root: Path,
    ref: Any,
    revision: Any,
    expected_hash: Any,
    label: str,
) -> RegistryArtifact:
    revision = _portable(revision, f"{label}.artifact_revision")
    if (
        not isinstance(expected_hash, str)
        or not root_writer.SHA256_RE.fullmatch(expected_hash)
    ):
        raise AgentRegistryError(f"{label}.artifact_sha256 is invalid")
    path = _resolve_regular(root, ref, f"{label}.artifact_ref")
    content = path.read_bytes()
    actual_hash = _sha256(content)
    if actual_hash != expected_hash:
        raise AgentRegistryError(
            f"{label} hash mismatch: expected {expected_hash}, got {actual_hash}"
        )
    return RegistryArtifact(
        ref=str(ref),
        revision=revision,
        sha256=actual_hash,
        path=path,
        content=content,
        value=_json_object(content, label),
    )


def _entry_artifact(
    agents_root: Path, entry: dict[str, Any], label: str
) -> RegistryArtifact:
    if set(entry) != {label + "_id", "spec_ref", "sha256", "role_class"} and not (
        label == "route" and set(entry) == {"route_id", "spec_ref", "sha256"}
    ):
        raise AgentRegistryError(f"{label} registry entry has unexpected fields")
    identity = _portable(entry[f"{label}_id"], f"{label}_id")
    return _artifact(
        agents_root,
        entry["spec_ref"],
        identity,
        entry["sha256"],
        f"{label} {identity}",
    )


def _under_allowed_root(
    candidate: str, allowed: tuple[PurePosixPath, ...]
) -> bool:
    relative = root_writer._validate_relative_path(candidate, "route write root")
    return any(root_writer._under_prefix(relative, root) for root in allowed)


def _check_acyclic(nodes: tuple[RouteNode, ...], route_id: str) -> None:
    by_id = {node.node_id: node for node in nodes}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise AgentRegistryError(f"route {route_id} contains a dependency cycle")
        if node_id in visited:
            return
        visiting.add(node_id)
        for dependency in by_id[node_id].depends_on:
            visit(dependency)
        visiting.remove(node_id)
        visited.add(node_id)

    for node in nodes:
        visit(node.node_id)


def role_contract_from_artifact(
    artifact: RegistryArtifact,
    expected_role_id: str,
) -> RoleContract:
    """Validate one current or frozen role artifact into an executable contract."""

    role_id = _portable(expected_role_id, "role_id")
    spec = artifact.value
    expected_role_fields = {
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
    if set(spec) != expected_role_fields:
        raise AgentRegistryError(f"role specification fields are invalid: {role_id}")
    if (
        artifact.revision != role_id
        or spec.get("role_id") != role_id
        or spec.get("spec_version") != "1.0"
    ):
        raise AgentRegistryError(f"role identity/version mismatch: {role_id}")
    if spec.get("role_class") not in {"core", "specialist"}:
        raise AgentRegistryError(f"role class is invalid: {role_id}")

    authority = _object(spec.get("authority"), f"role {role_id} authority")
    expected_authority_fields = {
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
    if set(authority) != expected_authority_fields:
        raise AgentRegistryError(f"role authority fields are invalid: {role_id}")
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
        raise AgentRegistryError(f"role authority is too broad: {role_id}")

    handoffs = _object(spec.get("handoff_contracts"), f"role {role_id} handoffs")
    if handoffs != {"task": "task-envelope@2", "result": "result-envelope@2"}:
        raise AgentRegistryError(f"role handoff contracts are stale: {role_id}")

    raw_outputs = spec.get("outputs")
    if not isinstance(raw_outputs, list) or not raw_outputs:
        raise AgentRegistryError(f"role has no output contracts: {role_id}")
    output_contracts: set[str] = set()
    output_ids: set[str] = set()
    for index, raw_output in enumerate(raw_outputs):
        output = _object(raw_output, f"role {role_id} output[{index}]")
        if set(output) != {"output_id", "contract", "purpose"}:
            raise AgentRegistryError(f"role output fields are invalid: {role_id}")
        output_id = _portable(output.get("output_id"), "role output_id")
        contract = _contract(output.get("contract"), "role output contract")
        if (
            output_id in output_ids
            or contract in output_contracts
            or not isinstance(output.get("purpose"), str)
            or len(output["purpose"].strip()) < 10
        ):
            raise AgentRegistryError(
                f"role output contract is invalid: {role_id}/{contract}"
            )
        output_ids.add(output_id)
        output_contracts.add(contract)
    return RoleContract(
        role_id=role_id,
        artifact=artifact,
        output_contracts=frozenset(output_contracts),
    )


def route_contract_from_artifact(
    artifact: RegistryArtifact,
    expected_route_id: str,
    *,
    known_role_ids: set[str],
    role_outputs: dict[str, frozenset[str]],
    project_config_path: Path,
    allowed_roots: tuple[PurePosixPath, ...],
    profile_role_ids: set[str] | None = None,
    ownership_node_ids: set[str] | None = None,
) -> RouteContract:
    """Validate one current or frozen route without consulting mutable role bytes."""

    route_id = _portable(expected_route_id, "route_id")
    spec = artifact.value
    expected_route_fields = {
        "route_version",
        "route_id",
        "display_name",
        "purpose",
        "activation",
        "context_requirements",
        "nodes",
        "final_product",
        "parallelism",
        "failure_policy",
        "terminal_review",
    }
    if set(spec) != expected_route_fields:
        raise AgentRegistryError(f"route specification fields are invalid: {route_id}")
    if (
        artifact.revision != route_id
        or spec.get("route_id") != route_id
        or spec.get("route_version") != "1.0"
    ):
        raise AgentRegistryError(f"route identity/version mismatch: {route_id}")
    raw_nodes = spec.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise AgentRegistryError(f"route {route_id} has no nodes")

    nodes: list[RouteNode] = []
    seen_nodes: set[str] = set()
    for raw_node in raw_nodes:
        node = _object(raw_node, f"route {route_id} node")
        if set(node) != {
            "node_id",
            "role_id",
            "depends_on",
            "optional",
            "run_when",
            "objective",
            "output_contract",
            "deliverable_contract",
            "allowed_write_roots",
        }:
            raise AgentRegistryError(f"route {route_id} node fields are invalid")
        node_id = _portable(node.get("node_id"), "node_id")
        role_id = _portable(node.get("role_id"), "node role_id")
        if node_id in seen_nodes:
            raise AgentRegistryError(f"duplicate route node: {route_id}/{node_id}")
        seen_nodes.add(node_id)
        if role_id not in known_role_ids:
            raise AgentRegistryError(
                f"route {route_id} references unknown role {role_id}"
            )
        if profile_role_ids is not None and role_id not in profile_role_ids:
            raise AgentRegistryError(
                f"route {route_id} role is not enabled by the profile: {role_id}"
            )
        dependencies = node.get("depends_on")
        if (
            not isinstance(dependencies, list)
            or not all(isinstance(item, str) for item in dependencies)
            or len(set(dependencies)) != len(dependencies)
        ):
            raise AgentRegistryError(
                f"route {route_id}/{node_id} has invalid dependencies"
            )
        roots = node.get("allowed_write_roots")
        if not isinstance(roots, list) or not roots:
            raise AgentRegistryError(f"route {route_id}/{node_id} has no write roots")
        try:
            roots_allowed = all(
                isinstance(root, str) and _under_allowed_root(root, allowed_roots)
                for root in roots
            )
        except root_writer.WriterError as exc:
            raise AgentRegistryError(str(exc)) from exc
        if not roots_allowed:
            raise AgentRegistryError(
                f"route {route_id}/{node_id} requests a write root outside the profile"
            )
        output_contract = _contract(node.get("output_contract"), "output_contract")
        try:
            load_registered_schema(project_config_path, output_contract)
        except SuiteConfigurationError as exc:
            raise AgentRegistryError(
                f"route {route_id}/{node_id} output contract is not registered: {exc}"
            ) from exc
        deliverable_contract = _contract(
            node.get("deliverable_contract"), "deliverable_contract"
        )
        requires_ownership = (
            ownership_node_ids is None or node_id in ownership_node_ids
        )
        if requires_ownership:
            outputs = role_outputs.get(role_id)
            if outputs is None or deliverable_contract not in outputs:
                raise AgentRegistryError(
                    "route asks a role for an unowned deliverable: "
                    f"{route_id}/{node_id}/{role_id}/{deliverable_contract}"
                )
        objective = node.get("objective")
        if not isinstance(objective, str) or len(objective.strip()) < 10:
            raise AgentRegistryError(
                f"route {route_id}/{node_id} objective is too short"
            )
        optional = node.get("optional")
        run_when = node.get("run_when")
        if not isinstance(optional, bool) or not isinstance(run_when, str) or not run_when.strip():
            raise AgentRegistryError(
                f"route {route_id}/{node_id} activation contract is invalid"
            )
        if optional and run_when.strip().casefold() == "always":
            raise AgentRegistryError(
                f"route {route_id}/{node_id} optional activation is unconditional"
            )
        nodes.append(
            RouteNode(
                node_id=node_id,
                role_id=role_id,
                depends_on=tuple(dependencies),
                objective=objective,
                output_contract=output_contract,
                deliverable_contract=deliverable_contract,
                allowed_write_roots=tuple(roots),
                optional=optional,
                run_when=run_when,
            )
        )

    node_ids = {node.node_id for node in nodes}
    for node in nodes:
        missing = sorted(set(node.depends_on) - node_ids)
        if missing:
            raise AgentRegistryError(
                f"route {route_id}/{node.node_id} has unknown dependencies: "
                + ", ".join(missing)
            )
    frozen_nodes = tuple(nodes)
    _check_acyclic(frozen_nodes, route_id)
    first = frozen_nodes[0]
    if (
        first.node_id != "coordinate"
        or first.role_id != "growth-coordinator"
        or first.optional
        or first.depends_on
    ):
        raise AgentRegistryError(f"route {route_id} has an invalid control entry node")

    terminal = _object(spec.get("terminal_review"), f"route {route_id} terminal review")
    if set(terminal) != {
        "governance_node",
        "governance_contract",
        "required_governance_result",
        "review_item_contract",
        "terminal_state",
        "human_actions",
        "external_side_effects",
        "may_publish",
    }:
        raise AgentRegistryError(f"route {route_id} terminal fields are invalid")
    governance_node = _portable(terminal.get("governance_node"), "governance_node")
    if governance_node not in node_ids:
        raise AgentRegistryError(f"route {route_id} governance node does not exist")
    if (
        terminal.get("governance_contract") != "governance-verdict@1"
        or terminal.get("required_governance_result") != "pass"
        or terminal.get("review_item_contract") != "outbox-review-item@1"
        or terminal.get("terminal_state") != "review_ready"
        or set(terminal.get("human_actions", [])) != {"approve", "decline", "note"}
        or terminal.get("may_publish") is not False
        or terminal.get("external_side_effects") is not False
    ):
        raise AgentRegistryError(f"route {route_id} terminal contract is invalid")

    final_product = _object(spec.get("final_product"), f"route {route_id} final product")
    if set(final_product) != {"producer_node", "contract", "supporting_contracts"}:
        raise AgentRegistryError(f"route {route_id} final-product fields are invalid")
    final_product_node = _portable(
        final_product.get("producer_node"), "final product producer_node"
    )
    if final_product_node not in node_ids:
        raise AgentRegistryError(
            f"route {route_id} final-product producer does not exist"
        )
    final_product_contract = _contract(
        final_product.get("contract"), "final product contract"
    )
    nodes_by_id = {node.node_id: node for node in frozen_nodes}
    governance = nodes_by_id[governance_node]
    producer = nodes_by_id[final_product_node]
    if (
        governance.optional
        or governance.role_id != "governance-reviewer"
        or governance.deliverable_contract != "governance-verdict@1"
        or final_product_node not in governance.depends_on
    ):
        raise AgentRegistryError(f"route {route_id} Governance boundary is invalid")
    if (
        producer.optional
        or producer.role_id == "governance-reviewer"
        or producer.deliverable_contract != final_product_contract
    ):
        raise AgentRegistryError(f"route {route_id} final-product boundary is invalid")

    context_requirements = spec.get("context_requirements")
    if (
        not isinstance(context_requirements, list)
        or not context_requirements
        or len(context_requirements) != len(set(context_requirements))
        or not set(context_requirements).issubset(
            {
                "facts",
                "prohibited_claims",
                "audience",
                "market",
                "funnel",
                "metrics",
                "channels",
                "authority",
            }
        )
    ):
        raise AgentRegistryError(f"route {route_id} has no context requirements")
    parallelism = _object(spec.get("parallelism"), f"route {route_id} parallelism")
    if set(parallelism) != {
        "max_concurrent_workhorses",
        "only_independent_nodes",
    }:
        raise AgentRegistryError(f"route {route_id} parallelism fields are invalid")
    max_concurrent = parallelism.get("max_concurrent_workhorses")
    if (
        not isinstance(max_concurrent, int)
        or isinstance(max_concurrent, bool)
        or not 1 <= max_concurrent <= 8
    ):
        raise AgentRegistryError(
            f"route {route_id} max_concurrent_workhorses is invalid"
        )
    if parallelism.get("only_independent_nodes") is not True:
        raise AgentRegistryError(
            f"route {route_id} must allow only independent parallel nodes"
        )
    failure_policy = _object(
        spec.get("failure_policy"), f"route {route_id} failure policy"
    )
    if (
        set(failure_policy)
        != {
            "dependent_nodes",
            "independent_nodes",
            "ambiguous_result",
            "semantic_revisions",
        }
        or failure_policy.get("dependent_nodes") != "blocked-or-skipped"
        or failure_policy.get("independent_nodes") != "may-continue"
        or failure_policy.get("ambiguous_result")
        != "needs_attention-no-blind-retry"
        or not isinstance(failure_policy.get("semantic_revisions"), int)
        or isinstance(failure_policy.get("semantic_revisions"), bool)
        or not 0 <= failure_policy["semantic_revisions"] <= 2
    ):
        raise AgentRegistryError(f"route {route_id} failure policy is invalid")
    return RouteContract(
        route_id=route_id,
        artifact=artifact,
        context_requirements=tuple(context_requirements),
        nodes=frozen_nodes,
        final_product_contract=final_product_contract,
        final_product_node=final_product_node,
        governance_node=governance_node,
        max_concurrent_workhorses=max_concurrent,
        only_independent_nodes=True,
    )


def load_agent_registry(
    workspace_path: Path,
    project_config_path: Path,
    registry_pointer: dict[str, Any],
) -> ResolvedAgentRegistry:
    """Resolve the exact global registry against one selected project profile."""

    try:
        workspace = root_writer._validate_workspace(workspace_path)
        profile = root_writer.load_project_profile(project_config_path)
        root_writer._validate_profile_package(workspace, project_config_path, profile)
    except root_writer.WriterError as exc:
        raise AgentRegistryError(str(exc)) from exc
    if set(registry_pointer) != {
        "artifact_ref",
        "artifact_revision",
        "artifact_sha256",
    }:
        raise AgentRegistryError("agent_registry pointer has unexpected fields")
    if not str(registry_pointer["artifact_ref"]).startswith("agents/"):
        raise AgentRegistryError("agent registry must live below workspace/agents")

    registry_artifact = _artifact(
        workspace,
        registry_pointer["artifact_ref"],
        registry_pointer["artifact_revision"],
        registry_pointer["artifact_sha256"],
        "agent registry",
    )
    registry = registry_artifact.value
    if registry.get("registry_version") != "1.0":
        raise AgentRegistryError("unsupported agent registry version")
    if registry.get("registry_revision") != registry_artifact.revision:
        raise AgentRegistryError("agent registry revision does not match its pointer")
    invariants = _object(registry.get("invariants"), "agent registry invariants")
    for invariant in (
        "one_project_per_run",
        "project_context_required",
        "roles_are_read_only",
        "root_only_persistence",
        "governance_before_review",
        "human_review_exact_revision",
        "approval_is_not_publication",
    ):
        if invariants.get(invariant) is not True:
            raise AgentRegistryError(f"required registry invariant is false: {invariant}")
    if invariants.get("publisher_present") is not False:
        raise AgentRegistryError("agent registry may not contain a publisher")

    agents_root = workspace / "agents"
    role_entries = registry.get("roles")
    if not isinstance(role_entries, list) or not role_entries:
        raise AgentRegistryError("agent registry roles must be a non-empty array")
    roles: dict[str, RoleContract] = {}
    for raw_entry in role_entries:
        entry = _object(raw_entry, "role registry entry")
        artifact = _entry_artifact(agents_root, entry, "role")
        role_id = _portable(entry["role_id"], "role_id")
        if role_id in roles:
            raise AgentRegistryError(f"duplicate role in registry: {role_id}")
        if entry["role_class"] != artifact.value.get("role_class"):
            raise AgentRegistryError(f"role class mismatch: {role_id}")
        roles[role_id] = role_contract_from_artifact(artifact, role_id)

    route_entries = registry.get("routes")
    if not isinstance(route_entries, list) or not route_entries:
        raise AgentRegistryError("agent registry routes must be a non-empty array")
    routes: dict[str, RouteContract] = {}
    profile_roles = set(profile.get("role_ids", []))
    enabled_routes = set(profile.get("enabled_routes", []))
    for raw_entry in route_entries:
        entry = _object(raw_entry, "route registry entry")
        artifact = _entry_artifact(agents_root, entry, "route")
        route_id = _portable(entry["route_id"], "route_id")
        if route_id in routes:
            raise AgentRegistryError(f"duplicate route in registry: {route_id}")
        routes[route_id] = route_contract_from_artifact(
            artifact,
            route_id,
            known_role_ids=set(roles),
            role_outputs={
                role_id: role.output_contracts for role_id, role in roles.items()
            },
            project_config_path=project_config_path,
            allowed_roots=profile["_allowed_roots"],
            profile_role_ids=(profile_roles if route_id in enabled_routes else None),
        )

    return ResolvedAgentRegistry(
        artifact=registry_artifact,
        roles=roles,
        routes=routes,
    )
