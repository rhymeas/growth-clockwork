#!/usr/bin/env python3
"""Resolve one immutable, profile-confined Project Context Pack.

The resolver treats context manifests and their section/source references as data,
never as instructions. Every path must stay below the selected project profile,
must not traverse a symlink, and must match its pinned SHA-256. A `ready` label is
therefore insufficient on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from pipeline import root_writer
from pipeline.validate_json_suites import (
    SuiteConfigurationError,
    ValidationError,
    validate_registered_instance,
)


CONTEXT_KINDS = (
    "facts",
    "prohibited_claims",
    "audience",
    "market",
    "funnel",
    "metrics",
    "channels",
    "authority",
)

# Discovery can investigate a question without treating missing product or market
# evidence as established truth. Governing policy must still be current and usable.
DISCOVERY_CONTEXT_KINDS = ("prohibited_claims", "authority")


class ContextResolutionError(ValueError):
    """Raised when profile context cannot be trusted for a run."""


@dataclass(frozen=True)
class ResolvedArtifact:
    ref: str
    revision: str
    sha256: str
    path: Path
    content: bytes


@dataclass(frozen=True)
class ResolvedSection:
    kind: str
    artifact: ResolvedArtifact
    value: dict[str, Any]
    sources: tuple[ResolvedArtifact, ...]


@dataclass(frozen=True)
class ResolvedContext:
    manifest: ResolvedArtifact
    value: dict[str, Any]
    sections: dict[str, ResolvedSection]

    @property
    def fixture_only(self) -> bool:
        return any(
            section.value["fixture_only"] or section.value["synthetic"]
            for section in self.sections.values()
        )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContextResolutionError(f"{label} must be a JSON object")
    return value


def _read_json_object(content: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContextResolutionError(f"{label} is not valid UTF-8 JSON: {exc}") from exc
    return _require_object(value, label)


def _format_validation_errors(errors: Iterable[ValidationError]) -> str:
    return "; ".join(
        f"{error.instance_path} [{error.rule}]: {error.message}"
        for error in errors
    )


def _resolve_regular_file(profile_root: Path, raw_ref: Any, label: str) -> Path:
    if not isinstance(raw_ref, str) or not raw_ref:
        raise ContextResolutionError(f"{label} must be a non-empty relative path")
    if "\\" in raw_ref or "\x00" in raw_ref:
        raise ContextResolutionError(f"{label} contains forbidden characters")
    relative = Path(raw_ref)
    if relative.is_absolute() or not relative.parts or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise ContextResolutionError(f"{label} must stay inside the project profile")

    current = profile_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ContextResolutionError(f"{label} may not traverse a symlink: {raw_ref}")
    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(profile_root)
    except (OSError, ValueError) as exc:
        raise ContextResolutionError(
            f"{label} is missing or escapes the project profile: {raw_ref}"
        ) from exc
    if not resolved.is_file():
        raise ContextResolutionError(f"{label} is not a regular file: {raw_ref}")
    return resolved


def _pinned_artifact(
    profile_root: Path,
    ref: Any,
    revision: Any,
    expected_hash: Any,
    label: str,
) -> ResolvedArtifact:
    if not isinstance(revision, str) or not revision:
        raise ContextResolutionError(f"{label}.revision must be a non-empty string")
    if (
        not isinstance(expected_hash, str)
        or len(expected_hash) != 64
        or any(char not in "0123456789abcdef" for char in expected_hash)
    ):
        raise ContextResolutionError(f"{label}.sha256 must be lowercase SHA-256")
    path = _resolve_regular_file(profile_root, ref, f"{label}.ref")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ContextResolutionError(f"cannot read {label}: {exc}") from exc
    actual_hash = _sha256(content)
    if actual_hash != expected_hash:
        raise ContextResolutionError(
            f"{label} hash mismatch: expected {expected_hash}, got {actual_hash}"
        )
    return ResolvedArtifact(
        ref=str(ref),
        revision=revision,
        sha256=actual_hash,
        path=path,
        content=content,
    )


def resolve_project_context(
    project_config_path: Path,
    context_pointer: dict[str, Any],
    *,
    run_mode: str,
    required_kinds: Iterable[str] = CONTEXT_KINDS,
) -> ResolvedContext:
    """Resolve and verify a context manifest, eight sections, and their sources."""

    if run_mode not in {"fixture", "live", "discovery"}:
        raise ContextResolutionError("run_mode must be fixture, live, or discovery")
    try:
        profile = root_writer.load_project_profile(project_config_path)
    except root_writer.WriterError as exc:
        raise ContextResolutionError(str(exc)) from exc
    profile_root = project_config_path.resolve().parent

    pointer = _require_object(context_pointer, "project_context pointer")
    if set(pointer) != {"artifact_ref", "artifact_revision", "artifact_sha256"}:
        raise ContextResolutionError(
            "project_context pointer must contain only artifact_ref, "
            "artifact_revision, and artifact_sha256"
        )
    if not str(pointer["artifact_ref"]).startswith("context/manifests/"):
        raise ContextResolutionError(
            "project_context manifest must live below context/manifests"
        )
    manifest_artifact = _pinned_artifact(
        profile_root,
        pointer["artifact_ref"],
        pointer["artifact_revision"],
        pointer["artifact_sha256"],
        "project context manifest",
    )
    manifest = _read_json_object(
        manifest_artifact.content, "project context manifest"
    )
    try:
        errors = validate_registered_instance(
            project_config_path,
            "project-context-manifest@1",
            manifest,
        )
    except SuiteConfigurationError as exc:
        raise ContextResolutionError(str(exc)) from exc
    if errors:
        raise ContextResolutionError(
            "project context manifest failed schema validation: "
            + _format_validation_errors(errors)
        )
    if manifest["context_revision"] != manifest_artifact.revision:
        raise ContextResolutionError(
            "project context pointer revision does not match the manifest"
        )
    if manifest["status"] != "ready" and not (
        run_mode == "discovery" and manifest["status"] == "draft"
    ):
        raise ContextResolutionError(
            f"project context is {manifest['status']}, not ready"
        )

    sections: dict[str, ResolvedSection] = {}
    for item in manifest["sections"]:
        kind = item["kind"]
        if kind in sections:
            raise ContextResolutionError(f"duplicate context section: {kind}")
        artifact = _pinned_artifact(
            profile_root,
            item["artifact_ref"],
            item["artifact_revision"],
            item["artifact_sha256"],
            f"context section {kind}",
        )
        value = _read_json_object(artifact.content, f"context section {kind}")
        try:
            errors = validate_registered_instance(
                project_config_path,
                "context-section-envelope@1",
                value,
            )
        except SuiteConfigurationError as exc:
            raise ContextResolutionError(str(exc)) from exc
        if errors:
            raise ContextResolutionError(
                f"context section {kind} failed schema validation: "
                + _format_validation_errors(errors)
            )
        if value["section_kind"] != kind:
            raise ContextResolutionError(
                f"context section kind mismatch: expected {kind}"
            )
        if value["section_revision"] != artifact.revision:
            raise ContextResolutionError(
                f"context section revision mismatch: {kind}"
            )

        sources: list[ResolvedArtifact] = []
        for source in value["source_refs"]:
            sources.append(
                _pinned_artifact(
                    profile_root,
                    source["artifact_ref"],
                    source["artifact_revision"],
                    source["artifact_sha256"],
                    f"context section {kind} source {source['source_id']}",
                )
            )
        sections[kind] = ResolvedSection(
            kind=kind,
            artifact=artifact,
            value=value,
            sources=tuple(sources),
        )

    resolved = ResolvedContext(
        manifest=manifest_artifact,
        value=manifest,
        sections=sections,
    )
    if run_mode in {"live", "discovery"} and resolved.fixture_only:
        raise ContextResolutionError(
            f"synthetic or fixture-only context cannot be used for a {run_mode} run"
        )

    required = tuple(required_kinds)
    unknown = sorted(set(required) - set(CONTEXT_KINDS))
    if unknown:
        raise ContextResolutionError(
            f"unknown required context kind(s): {', '.join(unknown)}"
        )
    missing = sorted(set(required) - sections.keys())
    if missing:
        raise ContextResolutionError(
            f"required context section(s) missing: {', '.join(missing)}"
        )
    for kind in required:
        section = sections[kind]
        policy = section.value["usage_policy"]
        fixture_restriction = (
            run_mode == "fixture"
            and policy == "restricted"
            and section.value["fixture_only"] is True
        )
        discovery_restriction = (
            run_mode == "discovery"
            and policy == "restricted"
            and kind not in DISCOVERY_CONTEXT_KINDS
        )
        if policy != "allowed" and not (fixture_restriction or discovery_restriction):
            raise ContextResolutionError(
                f"context section {kind} is not allowed for autonomous work"
            )
        if section.value["material_state"] != "current":
            raise ContextResolutionError(
                f"context section {kind} is not current"
            )

    return resolved
