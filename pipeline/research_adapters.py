#!/usr/bin/env python3
"""Pinned selection and credential-free dispatch for research adapters.

The registry makes implemented, contract-only, and reference-only status
explicit so a Coordinator cannot mistake an OSS repository or API document for
a runnable integration. The only current entrypoint is manual evidence intake;
network collectors remain contracts or references rather than fake adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from pipeline import root_writer
from pipeline.audience_research import (
    ManualEvidencePlan,
    prepare_manual_evidence_observation,
)


PLATFORMS = {"tiktok", "youtube", "instagram", "pinterest", "x"}
ACCESS_CLASSES = {
    "official_public",
    "owner_authorized",
    "research_approved",
    "manual_evidence",
    "unofficial_modelled",
}
IMPLEMENTATION_STATUSES = {"implemented", "contract_only", "reference_only"}
IMPLEMENTED_ADAPTERS: dict[
    str,
    Callable[[Path, dict[str, Any], Mapping[str, bytes]], ManualEvidencePlan],
] = {
    "manual-evidence-intake": prepare_manual_evidence_observation,
}


class ResearchAdapterError(ValueError):
    """Raised when adapter capability or project authority is ambiguous."""


@dataclass(frozen=True)
class ResearchAdapterRegistry:
    revision: str
    sha256: str
    adapters: tuple[dict[str, Any], ...]


def _read_pinned_registry(
    workspace: Path, profile: dict[str, Any], pointer: dict[str, str]
) -> tuple[bytes, dict[str, Any]]:
    try:
        relative = root_writer._validate_relative_path(
            pointer["artifact_ref"], "research_adapters.artifact_ref"
        )
        path = root_writer._assert_no_symlink_path(workspace, relative)
    except (KeyError, root_writer.WriterError) as exc:
        raise ResearchAdapterError(str(exc)) from exc
    if not pointer["artifact_ref"].startswith("contracts/") or not path.is_file():
        raise ResearchAdapterError("research adapter registry must be a regular contracts file")
    content = path.read_bytes()
    actual = hashlib.sha256(content).hexdigest()
    if actual != pointer["artifact_sha256"]:
        raise ResearchAdapterError("research adapter registry hash mismatch")
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ResearchAdapterError(f"research adapter registry is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ResearchAdapterError("research adapter registry must be an object")
    return content, value


def load_research_adapter_registry(
    workspace_path: Path, profile_path: Path
) -> ResearchAdapterRegistry:
    try:
        workspace = root_writer._validate_workspace(workspace_path)
        profile = root_writer.load_project_profile(profile_path)
        root_writer._validate_profile_package(workspace, profile_path, profile)
    except root_writer.WriterError as exc:
        raise ResearchAdapterError(str(exc)) from exc
    pointer = profile.get("_research_adapters")
    if pointer is None:
        raise ResearchAdapterError("project profile does not pin research_adapters")
    content, value = _read_pinned_registry(workspace, profile, pointer)
    if set(value) != {"registry_version", "registry_revision", "invariants", "adapters"}:
        raise ResearchAdapterError("research adapter registry has unexpected fields")
    if value["registry_version"] != "1.0" or value["registry_revision"] != pointer["artifact_revision"]:
        raise ResearchAdapterError("research adapter registry revision mismatch")
    invariants = value["invariants"]
    expected_invariants = {
        "one_project_per_plan",
        "read_only_collection",
        "no_publish_send_spend_or_account_mutation",
        "platform_terms_are_separate_from_code_license",
        "unofficial_sources_are_discovery_only",
        "missing_data_is_not_zero",
        "samples_are_not_population_claims",
    }
    if not isinstance(invariants, dict) or set(invariants) != expected_invariants or not all(invariants.values()):
        raise ResearchAdapterError("research adapter registry invariants are incomplete")
    adapters = value["adapters"]
    if not isinstance(adapters, list) or not adapters:
        raise ResearchAdapterError("research adapter registry has no adapters")
    required = {
        "adapter_id", "kind", "platforms", "access_classes", "implementation_status",
        "default_enabled", "canonical_output", "allowed_governance_uses", "license_id",
        "source_url", "credential_mode", "network_access", "may_mutate_external_state",
        "variable_cost_class", "requirements", "limitations",
    }
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for raw in adapters:
        if not isinstance(raw, dict) or set(raw) != required:
            raise ResearchAdapterError("research adapter entry has unexpected fields")
        adapter_id = raw["adapter_id"]
        if not isinstance(adapter_id, str) or adapter_id in seen:
            raise ResearchAdapterError("research adapter IDs must be unique strings")
        seen.add(adapter_id)
        if not isinstance(raw["platforms"], list) or not set(raw["platforms"]).issubset(PLATFORMS):
            raise ResearchAdapterError(f"invalid platforms for adapter {adapter_id}")
        if not isinstance(raw["access_classes"], list) or not set(raw["access_classes"]).issubset(ACCESS_CLASSES):
            raise ResearchAdapterError(f"invalid access classes for adapter {adapter_id}")
        if raw["implementation_status"] not in IMPLEMENTATION_STATUSES:
            raise ResearchAdapterError(f"invalid implementation status for adapter {adapter_id}")
        if raw["may_mutate_external_state"] is not False:
            raise ResearchAdapterError(f"adapter may mutate external state: {adapter_id}")
        if not raw["requirements"] or not raw["limitations"]:
            raise ResearchAdapterError(f"adapter lacks requirements or limitations: {adapter_id}")
        if "unofficial_modelled" in raw["access_classes"] and (
            raw["default_enabled"] is not False
            or raw["canonical_output"] is not False
            or raw["allowed_governance_uses"] != ["idea_generation_only"]
            or raw["implementation_status"] != "reference_only"
        ):
            raise ResearchAdapterError(
                f"unofficial adapter must remain disabled and discovery-only: {adapter_id}"
            )
        normalized.append(dict(raw))
    registered_implementations = {
        item["adapter_id"]
        for item in normalized
        if item["implementation_status"] == "implemented"
    }
    if registered_implementations != set(IMPLEMENTED_ADAPTERS):
        raise ResearchAdapterError(
            "implemented adapter registry and executable entrypoints differ"
        )
    return ResearchAdapterRegistry(
        revision=value["registry_revision"],
        sha256=hashlib.sha256(content).hexdigest(),
        adapters=tuple(normalized),
    )


def plan_research_adapters(
    workspace_path: Path,
    profile_path: Path,
    *,
    platform: str,
    access_class: str,
    require_implemented: bool = True,
    allow_exploratory: bool = False,
) -> dict[str, Any]:
    """Return eligible candidates without executing a network or external tool."""

    if platform not in PLATFORMS:
        raise ResearchAdapterError("unsupported platform")
    if access_class not in ACCESS_CLASSES:
        raise ResearchAdapterError("unsupported access class")
    if access_class == "unofficial_modelled" and not allow_exploratory:
        raise ResearchAdapterError("unofficial sources require an explicit exploratory lane")
    profile = root_writer.load_project_profile(profile_path)
    registry = load_research_adapter_registry(workspace_path, profile_path)
    candidates = [
        adapter
        for adapter in registry.adapters
        if platform in adapter["platforms"]
        and access_class in adapter["access_classes"]
        and (not require_implemented or adapter["implementation_status"] == "implemented")
    ]
    if access_class == "unofficial_modelled":
        candidates = [
            adapter
            for adapter in candidates
            if adapter["allowed_governance_uses"] == ["idea_generation_only"]
            and not adapter["canonical_output"]
        ]
    return {
        "plan_version": "1.0",
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "registry_revision": registry.revision,
        "registry_sha256": registry.sha256,
        "platform": platform,
        "access_class": access_class,
        "require_implemented": require_implemented,
        "allow_exploratory": allow_exploratory,
        "candidates": candidates,
        "dispatchable": bool(candidates) and all(
            item["implementation_status"] == "implemented" for item in candidates
        ),
        "executed": False,
    }


def prepare_research_observation(
    workspace_path: Path,
    profile_path: Path,
    *,
    adapter_id: str,
    observation: dict[str, Any],
    evidence_artifacts: Mapping[str, bytes],
) -> ManualEvidencePlan:
    """Dispatch one implemented, read-only acquisition adapter.

    This boundary prepares exact output bytes only. It has no network handle and
    cannot persist, publish, message, spend, or mutate an external account.
    """

    registry = load_research_adapter_registry(workspace_path, profile_path)
    adapter = next(
        (item for item in registry.adapters if item["adapter_id"] == adapter_id),
        None,
    )
    if adapter is None:
        raise ResearchAdapterError(f"unknown research adapter: {adapter_id}")
    if adapter["implementation_status"] != "implemented":
        raise ResearchAdapterError(
            f"research adapter is not executable: {adapter_id}"
        )
    entrypoint = IMPLEMENTED_ADAPTERS.get(adapter_id)
    if entrypoint is None:
        raise ResearchAdapterError(
            f"research adapter has no executable entrypoint: {adapter_id}"
        )
    if observation.get("platform") not in adapter["platforms"]:
        raise ResearchAdapterError(
            f"research adapter does not support platform: {adapter_id}"
        )
    try:
        return entrypoint(profile_path, observation, evidence_artifacts)
    except ValueError as exc:
        raise ResearchAdapterError(str(exc)) from exc
