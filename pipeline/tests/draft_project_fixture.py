"""Build an empty, project-neutral draft profile for discovery contract tests.

Reusable schemas and registry pins come from the public Example package. Its
synthetic product material is not relabelled as real evidence: this helper writes
a new context containing actual local policy, empty facts, and explicit gaps.
It performs no research and supplies no invented customer or product claims.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Any


SOURCE_WORKSPACE = Path(__file__).resolve().parents[2]
MANIFEST_NAME = "CTX-RESEARCH-0001.draft-r1.json"


def _write(path: Path, value: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def create_draft_project(workspace: Path, directory: str = "research-example") -> Path:
    """Install a second isolated profile without depending on a private product."""
    target = workspace / "projects" / directory
    shutil.copytree(
        SOURCE_WORKSPACE / "projects/example", target,
        ignore=shutil.ignore_patterns("state", "context"),
    )
    profile_path = target / "project.json"
    profile = json.loads(profile_path.read_text())
    profile.update(
        project_id=directory, profile_revision=f"{directory}-profile-v1",
        display_name="Research Example", product_motion="research-project",
    )
    profile["writer"]["state_root"] = f"projects/{directory}/state"
    profile["task_id_pattern"] = r"^RE-[0-9]+(?:-T[0-9]{3,})?$"
    identity = {"project_id": profile["project_id"], "project_profile_revision": profile["profile_revision"]}
    policy = {
        **identity,
        "source_kind": "local-policy-and-gap-record",
        "autonomous_internal_work": True,
        "human_final_review_required": True,
        "external_side_effects_allowed": False,
        "unapproved_product_claims_allowed": False,
        "observed_customers": [], "approved_product_facts": [], "measured_outcomes": [],
    }
    policy_ref = "context/evidence/local-policy-draft-r1.json"
    policy_sha = _write(target / policy_ref, policy)
    definitions = {
        "facts": ("missing", "blocked", {"approved_facts": [], "candidate_facts": []}),
        "prohibited_claims": ("current", "allowed", {"prohibited_claims": ["Unapproved product behavior", "Invented customer evidence or performance"]}),
        "audience": ("candidate", "restricted", {"hypotheses": [], "validation_status": "not_run"}),
        "market": ("missing", "blocked", {"competitors": [], "category": None}),
        "funnel": ("missing", "blocked", {"events": []}),
        "metrics": ("missing", "blocked", {"live_values": []}),
        "channels": ("current", "restricted", {"active_channels": [], "candidate_format": "educational-text"}),
        "authority": ("current", "allowed", policy),
    }
    sections = []
    for kind, (material, usage, content) in definitions.items():
        revision = f"{kind.replace('_', '-')}-draft-r1"
        ref = f"context/sections/{kind.replace('_', '-')}/{revision}.json"
        section = {
            **identity, "envelope_version": "1.0", "section_kind": kind,
            "section_revision": revision, "material_state": material, "usage_policy": usage,
            "synthetic": False, "fixture_only": False, "publishable": False, "approval_ref": None,
            "source_refs": [{
                "source_id": "local-policy-and-gaps", "artifact_ref": policy_ref,
                "artifact_revision": "local-policy-draft-r1", "artifact_sha256": policy_sha,
                "source_state": "policy" if material == "current" else "gap_record",
            }],
            "unknowns": [] if material == "current" else ["No approved evidence has been supplied for this section."],
            "content": content, "created_at": "2026-09-01T00:00:00Z",
        }
        sections.append({"kind": kind, "artifact_ref": ref, "artifact_revision": revision, "artifact_sha256": _write(target / ref, section)})
    manifest_ref = f"context/manifests/{MANIFEST_NAME}"
    manifest = {
        **identity, "manifest_version": "1.0", "context_id": "CTX-RESEARCH-0001",
        "context_revision": "draft-r1", "status": "draft", "sections": sections,
        "authority": {"autonomous_internal_work": True, "human_final_review_required": True, "publish_credentials_available": False},
        "created_at": "2026-09-01T00:00:00Z",
    }
    profile["project_context"] = {"artifact_ref": manifest_ref, "artifact_revision": "draft-r1", "artifact_sha256": _write(target / manifest_ref, manifest)}
    _write(profile_path, profile)
    return profile_path


def rewrite_context_section(profile_path: Path, kind: str, changes: dict[str, Any]) -> None:
    """Change a temporary test section and repin its manifest and profile."""
    profile = json.loads(profile_path.read_text())
    manifest_path = profile_path.parent / profile["project_context"]["artifact_ref"]
    manifest = json.loads(manifest_path.read_text())
    pointer = next(item for item in manifest["sections"] if item["kind"] == kind)
    section_path = profile_path.parent / pointer["artifact_ref"]
    section = json.loads(section_path.read_text())
    section.update(changes)
    pointer["artifact_sha256"] = _write(section_path, section)
    profile["project_context"]["artifact_sha256"] = _write(manifest_path, manifest)
    _write(profile_path, profile)
