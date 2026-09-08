"""Versioned release-adapter port.

The core passes verified bytes and a schema-valid request into this port.  An
adapter returns an inert write plan; only the Root Writer may persist it.  A real
publisher is intentionally absent from this package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


ADAPTER_PORT_VERSION = "1.0"


@dataclass(frozen=True)
class AdapterPlan:
    """Credential-free immutable records prepared by one adapter."""

    request: dict[str, Any]
    output_ref: str
    output_content: bytes
    output_media_type: str
    receipt_ref: str
    receipt: dict[str, Any]
    proof_ref: str
    proof: dict[str, Any]
    outcome_ref: str
    outcome: dict[str, Any]


class ReleaseAdapterPort(Protocol):
    """Narrow v1 interface; adapters receive no credential or network handle."""

    adapter_id: str
    adapter_version: str
    port_version: str

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
        """Return an inert, deterministic plan for Root Writer persistence."""

