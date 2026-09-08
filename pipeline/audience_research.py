#!/usr/bin/env python3
"""Contract and semantic audit for cross-platform audience research.

Acquisition is adapter-specific. This module verifies that exact observation
bytes, coverage claims, evidence grades, and synthesis agree. Unofficial and
modelled sources remain usable for discovery, but cannot silently become
decision evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from pipeline import root_writer
from pipeline.validate_json_suites import (
    SuiteConfigurationError,
    validate_registered_instance,
)


OBSERVATION_SCHEMA_ID = "platform-observation@1"
PACKAGE_SCHEMA_ID = "audience-research-package@1"
RISKY_METHODS = {
    "third_party_scraping_service",
    "session_cookie_capture",
    "public_web_fallback",
    "model_inference",
}
IDEA_ONLY_TIERS = {
    "third_party_black_box",
    "unofficial_session",
    "model_inference",
}
MANUAL_ACCESS_METHODS = {
    "human_assisted_capture",
    "manual_export",
    "manual_public_observation",
}
MANUAL_SOURCE_TIERS = {
    "official_primary",
    "official_owner_data",
    "documented_oss_capture",
}


class AudienceResearchError(ValueError):
    """Raised for an unreadable or structurally invalid research artifact."""


@dataclass(frozen=True)
class ManualEvidencePlan:
    """Side-effect-free output from the manual evidence intake adapter."""

    adapter_id: str
    output_ref: str
    output_revision: str
    output_sha256: str
    output_content: bytes
    evidence_refs: tuple[dict[str, str], ...]


def _schema_messages(
    profile_path: Path, schema_id: str, instance: dict[str, Any]
) -> list[str]:
    try:
        errors = validate_registered_instance(profile_path, schema_id, instance)
    except SuiteConfigurationError as exc:
        return [str(exc)]
    return [f"{error.instance_path}: {error.message}" for error in errors]


def validate_platform_observation(
    profile_path: Path, observation: dict[str, Any]
) -> None:
    messages = _schema_messages(profile_path, OBSERVATION_SCHEMA_ID, observation)
    if messages:
        raise AudienceResearchError(
            f"Platform observation rejected: {messages[0]}"
        )
    sample = observation["sample"]
    if sample["items_included"] > sample["items_seen"]:
        raise AudienceResearchError("items_included cannot exceed items_seen")
    if observation["access_method"] in RISKY_METHODS and observation[
        "governance_use"
    ] != "idea_generation_only":
        raise AudienceResearchError(
            "Unofficial or modelled acquisition is idea_generation_only"
        )
    if observation["source_tier"] in IDEA_ONLY_TIERS and observation[
        "governance_use"
    ] != "idea_generation_only":
        raise AudienceResearchError(
            "Black-box, session, and inferred sources are idea_generation_only"
        )


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def prepare_manual_evidence_observation(
    profile_path: Path,
    observation: dict[str, Any],
    evidence_artifacts: Mapping[str, bytes],
) -> ManualEvidencePlan:
    """Prepare one exact, project-bound manual observation without writing it.

    The operator supplies already-redacted evidence bytes and the complete
    observation record. This adapter verifies project identity, the manual-only
    acquisition lane, schema semantics, and every evidence hash. It neither
    discovers platform content nor persists anything; the Root Writer remains
    the only component allowed to commit the returned bytes.
    """

    try:
        profile = root_writer.load_project_profile(profile_path)
    except root_writer.WriterError as exc:
        raise AudienceResearchError(str(exc)) from exc
    if not isinstance(observation, dict):
        raise AudienceResearchError("Manual observation must be a JSON object")
    if (
        observation.get("project_id") != profile["project_id"]
        or observation.get("project_profile_revision")
        != profile["profile_revision"]
    ):
        raise AudienceResearchError(
            "Manual observation belongs to another project profile"
        )
    if observation.get("collector_tool") != "manual-evidence-intake@1":
        raise AudienceResearchError(
            "Manual observation collector_tool must be manual-evidence-intake@1"
        )
    if observation.get("access_method") not in MANUAL_ACCESS_METHODS:
        raise AudienceResearchError(
            "Manual evidence intake accepts only manual acquisition methods"
        )
    if observation.get("source_tier") not in MANUAL_SOURCE_TIERS:
        raise AudienceResearchError(
            "Manual evidence intake cannot promote unofficial or modelled sources"
        )
    if (
        observation.get("governance_use") == "decision_evidence"
        and observation.get("commercial_use_clearance") != "cleared"
    ):
        raise AudienceResearchError(
            "Decision evidence requires explicit commercial-use clearance"
        )

    validate_platform_observation(profile_path, observation)
    evidence = observation["evidence"]
    refs = [item["artifact_ref"] for item in evidence]
    if len(refs) != len(set(refs)):
        raise AudienceResearchError("Manual observation repeats an evidence path")
    supplied = set(evidence_artifacts)
    if not all(isinstance(item, str) for item in supplied):
        raise AudienceResearchError("Manual evidence paths must be strings")
    expected = set(refs)
    if supplied != expected:
        missing = sorted(expected - supplied)
        extra = sorted(supplied - expected)
        details: list[str] = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if extra:
            details.append("unexpected: " + ", ".join(extra))
        raise AudienceResearchError(
            "Manual evidence byte set does not match declared refs ("
            + "; ".join(details)
            + ")"
        )
    for pointer in evidence:
        content = evidence_artifacts[pointer["artifact_ref"]]
        if not isinstance(content, bytes):
            raise AudienceResearchError(
                f"Manual evidence must be bytes: {pointer['artifact_ref']}"
            )
        if hashlib.sha256(content).hexdigest() != pointer["artifact_sha256"]:
            raise AudienceResearchError(
                f"Manual evidence hash mismatch: {pointer['artifact_ref']}"
            )
    if observation["collection_authority_ref"] not in expected:
        raise AudienceResearchError(
            "collection_authority_ref must be one of the exact evidence artifacts"
        )

    output_content = _canonical_json(observation)
    output_ref = (
        "records/audience-observations/"
        f"{observation['observation_id']}.json"
    )
    return ManualEvidencePlan(
        adapter_id="manual-evidence-intake@1",
        output_ref=output_ref,
        output_revision="r1",
        output_sha256=hashlib.sha256(output_content).hexdigest(),
        output_content=output_content,
        evidence_refs=tuple(dict(item) for item in evidence),
    )


def _decode_observation(content: bytes, artifact_ref: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AudienceResearchError(
            f"Invalid observation JSON at {artifact_ref}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise AudienceResearchError(
            f"Observation artifact must be a JSON object: {artifact_ref}"
        )
    return value


def audit_audience_research_package(
    profile_path: Path,
    package: dict[str, Any],
    observation_artifacts: Mapping[str, bytes],
) -> dict[str, Any]:
    """Audit package semantics and exact referenced observation bytes."""

    blockers = _schema_messages(profile_path, PACKAGE_SCHEMA_ID, package)
    warnings: list[str] = []
    if blockers:
        return {"status": "block", "blockers": blockers, "warnings": warnings}

    requested = set(package["requested_platforms"])
    coverage_by_platform: dict[str, dict[str, Any]] = {}
    for coverage in package["coverage"]:
        platform = coverage["platform"]
        if platform in coverage_by_platform:
            blockers.append(f"Duplicate coverage entry for {platform}")
        coverage_by_platform[platform] = coverage
        if coverage["status"] == "observed":
            if not coverage["observation_ids"] or coverage["reason"] is not None:
                blockers.append(
                    f"Observed coverage for {platform} needs observations and no reason"
                )
        elif coverage["status"] == "access_blocked":
            if coverage["observation_ids"] or not coverage["reason"]:
                blockers.append(
                    f"Blocked coverage for {platform} needs a reason and no observations"
                )

    if set(coverage_by_platform) != requested:
        blockers.append("Coverage must contain every requested platform exactly once")

    observations: dict[str, dict[str, Any]] = {}
    ref_platforms: dict[str, str] = {}
    for ref in package["observation_refs"]:
        artifact_ref = ref["artifact_ref"]
        content = observation_artifacts.get(artifact_ref)
        if content is None:
            blockers.append(f"Missing observation artifact bytes: {artifact_ref}")
            continue
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != ref["artifact_sha256"]:
            blockers.append(f"Observation hash mismatch: {artifact_ref}")
            continue
        try:
            observation = _decode_observation(content, artifact_ref)
            validate_platform_observation(profile_path, observation)
        except AudienceResearchError as exc:
            blockers.append(str(exc))
            continue
        observation_id = observation["observation_id"]
        if observation_id != ref["observation_id"]:
            blockers.append(f"Observation ID mismatch: {artifact_ref}")
            continue
        if observation["platform"] != ref["platform"]:
            blockers.append(f"Observation platform mismatch: {artifact_ref}")
            continue
        if observation_id in observations:
            blockers.append(f"Duplicate observation_id: {observation_id}")
            continue
        observations[observation_id] = observation
        ref_platforms[observation_id] = observation["platform"]
        if observation["governance_use"] == "idea_generation_only":
            warnings.append(
                f"{observation_id} is discovery-only and cannot support a factual claim"
            )
        if observation["sample"]["quota_limited"]:
            warnings.append(f"{observation_id} is quota-limited")
        if not observation["sample"]["pagination_complete"]:
            warnings.append(f"{observation_id} has incomplete pagination")

    for platform, coverage in coverage_by_platform.items():
        for observation_id in coverage["observation_ids"]:
            if observation_id not in observations:
                blockers.append(
                    f"Coverage references missing observation {observation_id}"
                )
            elif ref_platforms[observation_id] != platform:
                blockers.append(
                    f"Coverage platform mismatch for {observation_id}"
                )

    observed_platforms = {
        platform
        for platform, coverage in coverage_by_platform.items()
        if coverage["status"] == "observed"
    }
    expected_status = (
        "complete"
        if observed_platforms == requested and requested
        else "partial"
        if observed_platforms
        else "blocked"
    )
    if package["research_status"] != expected_status:
        blockers.append(
            f"research_status must be {expected_status} for the declared coverage"
        )
    if not observed_platforms:
        blockers.append("No requested platform has usable observations")

    insight_ids: set[str] = set()
    for insight in package["insights"]:
        insight_id = insight["insight_id"]
        if insight_id in insight_ids:
            blockers.append(f"Duplicate insight_id: {insight_id}")
        insight_ids.add(insight_id)
        support_ids = insight["supporting_observation_ids"]
        missing = sorted(set(support_ids) - observations.keys())
        if missing:
            blockers.append(
                f"{insight_id} references missing observations: {', '.join(missing)}"
            )
            continue
        derived_platforms = {ref_platforms[item] for item in support_ids}
        if set(insight["supporting_platforms"]) != derived_platforms:
            blockers.append(
                f"{insight_id} supporting_platforms do not match its observations"
            )
        support = [observations[item] for item in support_ids]
        idea_only = [
            item for item in support if item["governance_use"] == "idea_generation_only"
        ]
        if insight["classification"] == "cross_platform" and len(
            derived_platforms
        ) < 2:
            blockers.append(
                f"{insight_id} claims cross-platform support from fewer than two platforms"
            )
        if idea_only and (
            insight["classification"] != "directional"
            or insight["confidence"] != "low"
        ):
            blockers.append(
                f"{insight_id} uses discovery-only evidence and must stay directional/low"
            )
        if insight["confidence"] == "high":
            decision_platforms = {
                item["platform"]
                for item in support
                if item["governance_use"] == "decision_evidence"
                and item["commercial_use_clearance"] == "cleared"
            }
            if len(decision_platforms) < 2:
                blockers.append(
                    f"{insight_id} needs cleared decision evidence from two platforms for high confidence"
                )

    for section in ("audience_segments", "content_patterns", "recommendations"):
        for index, item in enumerate(package[section]):
            missing = sorted(set(item["evidence_insight_ids"]) - insight_ids)
            if missing:
                blockers.append(
                    f"{section}[{index}] references missing insights: {', '.join(missing)}"
                )

    if package["overall_confidence"] == "high":
        if expected_status != "complete":
            blockers.append("High overall confidence requires complete platform coverage")
        if not any(item["confidence"] == "high" for item in package["insights"]):
            blockers.append("High overall confidence requires a high-confidence insight")

    return {
        "status": "block" if blockers else "warn" if warnings else "pass",
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "requested_platforms": sorted(requested),
        "observed_platforms": sorted(observed_platforms),
        "observation_count": len(observations),
        "insight_count": len(insight_ids),
        "canonical_observation_count": sum(
            1
            for item in observations.values()
            if item["governance_use"] == "decision_evidence"
            and item["commercial_use_clearance"] == "cleared"
        ),
        "discovery_only_observation_count": sum(
            1
            for item in observations.values()
            if item["governance_use"] == "idea_generation_only"
        ),
    }
