"""Synthetic local-filesystem adapter for fixture proof only.

It copies exact approved bytes to a project-local staging path.  Even the
``simulated-live`` target remains private, local, and explicitly not publicly
live.  The adapter has no publisher client, credentials, network call, or shell.
"""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Any
import uuid

from pipeline.adapters.base import ADAPTER_PORT_VERSION, AdapterPlan


ADAPTER_ID = "local-filesystem-synthetic@1"
ADAPTER_VERSION = "1.0"
EXECUTION_NAMESPACE = uuid.UUID("2a3a85b6-a677-41fc-a2bd-df25287f82cb")
PROOF_NAMESPACE = uuid.UUID("55f2e0c8-3415-4566-ae2c-679344df0343")
OUTCOME_NAMESPACE = uuid.UUID("f32cfeb0-c5ab-42c0-92fb-ce089543106e")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _pointer(ref: str, revision: str, digest: str) -> dict[str, str]:
    return {
        "artifact_ref": ref,
        "artifact_revision": revision,
        "artifact_sha256": digest,
    }


class LocalFilesystemSyntheticAdapter:
    """Exact-byte copy adapter limited to ``product_motion=synthetic-fixture``."""

    adapter_id = ADAPTER_ID
    adapter_version = ADAPTER_VERSION
    port_version = ADAPTER_PORT_VERSION

    def prepare(
        self,
        *,
        profile: dict[str, Any],
        release_pointer: dict[str, str],
        release_package: dict[str, Any],
        source_bytes: bytes,
        source_media_type: str,
        request: dict[str, Any],
    ) -> AdapterPlan:
        if profile.get("product_motion") != "synthetic-fixture":
            raise ValueError(
                "local-filesystem synthetic adapter requires a synthetic-fixture project"
            )
        if request["adapter_id"] != self.adapter_id:
            raise ValueError("adapter request selected another adapter")
        if request["adapter_version"] != self.adapter_version:
            raise ValueError("adapter request version is unsupported")
        if request["target"] not in {"preview", "simulated-live"}:
            raise ValueError("synthetic adapter target must be preview or simulated-live")
        if request["external_side_effects"] is not False:
            raise ValueError("synthetic adapter forbids external side effects")
        if release_package["adapter_port_version"] != self.port_version:
            raise ValueError("release package adapter port version is unsupported")
        if release_package["contains_credentials"] is not False:
            raise ValueError("release package unexpectedly contains credentials")
        if release_package["external_side_effects"] is not False:
            raise ValueError("release package unexpectedly authorizes external effects")
        if _sha256(source_bytes) != release_package["artifact_sha256"]:
            raise ValueError("adapter source bytes do not match the approved release")

        request_id = request["request_id"]
        execution_id = str(uuid.uuid5(EXECUTION_NAMESPACE, request_id))
        proof_id = str(uuid.uuid5(PROOF_NAMESPACE, execution_id))
        outcome_id = str(uuid.uuid5(OUTCOME_NAMESPACE, proof_id))
        suffix = PurePosixPath(
            release_package["source_artifact"]["artifact_ref"]
        ).suffix or ".txt"
        output_ref = (
            f"staging/local-release/{request['target']}/{request_id}/"
            f"{release_package['artifact_id']}-{release_package['artifact_revision']}{suffix}"
        )
        output_pointer = _pointer(
            output_ref,
            release_package["artifact_revision"],
            _sha256(source_bytes),
        )
        receipt_ref = f"records/adapter-receipts/{execution_id}.json"
        receipt = {
            "adapter_receipt_version": "1.0",
            "execution_id": execution_id,
            "request_id": request_id,
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
            "release_package": release_pointer,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "target": request["target"],
            "status": "completed",
            "external_side_effects": False,
            "output_artifact": output_pointer,
            "completed_at": request["requested_at"],
        }
        # The caller hashes the canonical receipt bytes before validating proof.
        proof_ref = f"records/live-proofs/{proof_id}.json"
        outcome_ref = f"records/outcomes/{outcome_id}.json"
        return AdapterPlan(
            request=request,
            output_ref=output_ref,
            output_content=source_bytes,
            output_media_type=source_media_type,
            receipt_ref=receipt_ref,
            receipt=receipt,
            proof_ref=proof_ref,
            proof={
                "live_proof_version": "1.0",
                "proof_id": proof_id,
                "project_id": profile["project_id"],
                "project_profile_revision": profile["profile_revision"],
                "release_package": release_pointer,
                "adapter_receipt": {},
                "proof_scope": "local-fixture-only",
                "verification_status": "verified",
                "publicly_live": False,
                "external_side_effects": False,
                "evidence": [output_pointer],
                "observed_at": request["requested_at"],
            },
            outcome_ref=outcome_ref,
            outcome={
                "outcome_record_version": "1.0",
                "outcome_id": outcome_id,
                "project_id": profile["project_id"],
                "project_profile_revision": profile["profile_revision"],
                "release_package": release_pointer,
                "adapter_receipt": {},
                "live_proof": {},
                "outcome_type": "fixture-observation",
                "statement": (
                    "Exact approved bytes were reproduced in project-local fixture staging; "
                    "no public result or causal outcome was measured."
                ),
                "metrics": [],
                "causal_claim": False,
                "external_side_effects": False,
                "recorded_at": request["requested_at"],
            },
        )

