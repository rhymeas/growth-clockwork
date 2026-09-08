#!/usr/bin/env python3
"""Execute the versioned release-adapter port without a publisher.

V1 registers one implementation: a synthetic local-filesystem exact-byte copy
used only by fixture projects.  The module deliberately has no dynamic plugin
loading, credentials, network client, shell, or real publication target.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any
import uuid

from pipeline.adapters.base import ADAPTER_PORT_VERSION, ReleaseAdapterPort
from pipeline.adapters.local_filesystem import LocalFilesystemSyntheticAdapter
from pipeline import release_core


REQUEST_NAMESPACE = uuid.UUID("ab3d04ce-b566-4610-a60b-ec5e8a8f3075")
RFC3339_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
ADAPTERS: dict[str, ReleaseAdapterPort] = {
    LocalFilesystemSyntheticAdapter.adapter_id: LocalFilesystemSyntheticAdapter(),
}


class AdapterError(ValueError):
    """Fail-closed adapter request, validation, or persistence error."""


def _validate(
    profile_path: Path, schema_id: str, value: dict[str, Any], label: str
) -> None:
    try:
        release_core._validate_schema(profile_path, schema_id, value, label)
    except release_core.ReleaseError as exc:
        raise AdapterError(str(exc)) from exc


def execute_release_adapter(
    workspace_path: Path,
    profile_path: Path,
    *,
    release_package: dict[str, str],
    adapter_id: str,
    target: str,
    requested_at: str,
) -> dict[str, Any]:
    """Run one registered, credential-free adapter and persist separate proofs."""

    adapter = ADAPTERS.get(adapter_id)
    if adapter is None:
        raise AdapterError("adapter is not registered in the credential-free core")
    if adapter.port_version != ADAPTER_PORT_VERSION:
        raise AdapterError("adapter port version mismatch")
    if not isinstance(requested_at, str) or not RFC3339_UTC_RE.fullmatch(requested_at):
        raise AdapterError("requested_at must be RFC3339 UTC seconds")
    try:
        verified = release_core.load_verified_release_package(
            workspace_path, profile_path, release_package
        )
    except release_core.ReleaseError as exc:
        raise AdapterError(str(exc)) from exc
    profile = verified["profile"]
    package = verified["package"]
    pointer = verified["pointer"]
    request_identity = "\n".join(
        [
            profile["project_id"],
            profile["profile_revision"],
            package["release_id"],
            adapter.adapter_id,
            adapter.adapter_version,
            target,
            requested_at,
        ]
    )
    request_id = str(uuid.uuid5(REQUEST_NAMESPACE, request_identity))
    request = {
        "adapter_request_version": "1.0",
        "request_id": request_id,
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "release_package": pointer,
        "adapter_id": adapter.adapter_id,
        "adapter_version": adapter.adapter_version,
        "target": target,
        "external_side_effects": False,
        "requested_at": requested_at,
    }
    _validate(verified["profile_path"], "adapter-request@1", request, "adapter request")
    try:
        plan = adapter.prepare(
            profile=profile,
            release_pointer=pointer,
            release_package=package,
            source_bytes=verified["bundle"]["artifact_bytes"],
            source_media_type=verified["bundle"]["artifact_media_type"],
            request=request,
        )
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc

    request_content = release_core._canonical_json(request)
    receipt_content = release_core._canonical_json(plan.receipt)
    receipt_pointer = release_core._pointer(
        plan.receipt_ref, plan.receipt["execution_id"], receipt_content
    )
    proof = dict(plan.proof)
    proof["adapter_receipt"] = receipt_pointer
    proof_content = release_core._canonical_json(proof)
    proof_pointer = release_core._pointer(plan.proof_ref, proof["proof_id"], proof_content)
    outcome = dict(plan.outcome)
    outcome["adapter_receipt"] = receipt_pointer
    outcome["live_proof"] = proof_pointer
    outcome_content = release_core._canonical_json(outcome)

    _validate(verified["profile_path"], "adapter-receipt@1", plan.receipt, "adapter receipt")
    _validate(verified["profile_path"], "live-proof@1", proof, "live proof")
    _validate(verified["profile_path"], "release-outcome@1", outcome, "release outcome")
    request_ref = f"records/adapter-requests/{request_id}.json"
    writes = [
        release_core._write_item(request_ref, request_content),
        release_core._write_item(
            plan.output_ref, plan.output_content, plan.output_media_type
        ),
        release_core._write_item(plan.receipt_ref, receipt_content),
        release_core._write_item(plan.proof_ref, proof_content),
        release_core._write_item(plan.outcome_ref, outcome_content),
    ]
    run_id = f"adapter-{request_id.replace('-', '')[:24]}"
    writer_request = release_core._writer_request(
        profile, run_id, plan.receipt["execution_id"], writes
    )
    existed = release_core._state_bytes(
        verified["workspace"], profile, request_ref, missing_ok=True
    )
    try:
        root_receipt = release_core._apply_request(
            verified["workspace"], verified["profile_path"], writer_request
        )
    except release_core.ReleaseError as exc:
        raise AdapterError(str(exc)) from exc
    expected_paths = {item["path"] for item in writes}
    receipt_paths = {item["path"] for item in root_receipt["writes"]}
    if receipt_paths != expected_paths:
        raise AdapterError("Root Writer receipt does not cover the full adapter transaction")
    persisted_output = release_core._state_bytes(
        verified["workspace"], profile, plan.output_ref
    )
    if persisted_output != verified["bundle"]["artifact_bytes"]:
        raise AdapterError("local adapter output does not preserve exact approved bytes")
    return {
        "status": "completed",
        "idempotent_replay": existed == request_content,
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "adapter_request": release_core._pointer(
            request_ref, request_id, request_content
        ),
        "adapter_receipt": receipt_pointer,
        "live_proof": proof_pointer,
        "outcome": release_core._pointer(
            plan.outcome_ref, outcome["outcome_id"], outcome_content
        ),
        "output_artifact": plan.receipt["output_artifact"],
        "external_side_effects": False,
        "publicly_live": False,
        "writer_receipt": root_receipt,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--project-config", required=True, type=Path)
    parser.add_argument("--release-package-ref", required=True)
    parser.add_argument("--release-package-revision", required=True)
    parser.add_argument("--release-package-sha256", required=True)
    parser.add_argument("--adapter-id", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--requested-at", required=True)
    args = parser.parse_args(argv)
    try:
        result = execute_release_adapter(
            args.workspace,
            args.project_config,
            release_package={
                "artifact_ref": args.release_package_ref,
                "artifact_revision": args.release_package_revision,
                "artifact_sha256": args.release_package_sha256,
            },
            adapter_id=args.adapter_id,
            target=args.target,
            requested_at=args.requested_at,
        )
    except AdapterError as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
