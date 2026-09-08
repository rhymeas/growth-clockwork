#!/usr/bin/env python3
"""Validation boundary for optional, rebuildable project code graphs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pipeline.validate_json_suites import (
    SuiteConfigurationError,
    validate_registered_instance,
)


SCHEMA_ID = "code-graph-index@1"
EXPECTED_LICENSES = {
    "scip": "Apache-2.0",
    "codebase-memory-mcp": "MIT",
    "joern": "Apache-2.0",
    "gitnexus": "PolyForm-Noncommercial-1.0.0",
}


class CodeGraphError(ValueError):
    """Raised when an index receipt crosses the code-intelligence boundary."""


def assess_code_graph_index(
    profile_path: Path, index: dict[str, Any]
) -> dict[str, Any]:
    """Validate an index receipt and return a use-readiness verdict.

    A valid receipt can still be ``warn`` when it represents a dirty or partial
    source state. Consumers must pin the exact receipt and source revision.
    """

    try:
        errors = validate_registered_instance(profile_path, SCHEMA_ID, index)
    except SuiteConfigurationError as exc:
        return {"status": "block", "blockers": [str(exc)], "warnings": []}
    blockers = [f"{error.instance_path}: {error.message}" for error in errors]
    warnings: list[str] = []
    if blockers:
        return {"status": "block", "blockers": blockers, "warnings": warnings}

    adapter = index["adapter"]
    expected = EXPECTED_LICENSES.get(adapter["id"])
    if expected is not None and adapter["license_id"] != expected:
        blockers.append(
            f"{adapter['id']} must declare its verified {expected} license"
        )
    if adapter["id"] == "custom" and adapter["license_id"] != "custom-reviewed":
        blockers.append("A custom adapter needs custom-reviewed licensing")
    if adapter["id"] == "gitnexus" and not blockers:
        warnings.append(
            "GitNexus is source-available under PolyForm Noncommercial 1.0.0; "
            "use is noncommercial-only unless separately licensed"
        )
    if index["source_state"] in {"dirty_worktree", "unknown"}:
        warnings.append(
            "Code graph is navigation-only because source state is not a clean commit or snapshot"
        )
    if index["coverage"]["files_indexed"] == 0:
        blockers.append("Code graph indexed zero files")
    if index["coverage"]["symbols_indexed"] == 0:
        warnings.append("Code graph contains no symbols")

    report = {
        "status": "block" if blockers else "warn" if warnings else "pass",
        "blockers": blockers,
        "warnings": warnings,
        "adapter_id": adapter["id"],
        "source_revision": index["source_revision"],
        "rebuildable": True,
        "canonical_memory": False,
    }
    if adapter["id"] == "gitnexus":
        report["license_restrictions"] = ["noncommercial-only"]
    return report
