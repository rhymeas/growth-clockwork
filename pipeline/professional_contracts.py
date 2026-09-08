#!/usr/bin/env python3
"""Load and enforce exact-byte professional deliverable contracts.

The selected project profile pins both the common deliverable schema and the
route-specific requirements catalog:

    "professional_contracts": {
      "schema": {
        "artifact_ref": "contracts/professional-deliverable.schema.json",
        "artifact_revision": "professional-deliverable-v1",
        "artifact_sha256": "..."
      },
      "requirements": {
        "artifact_ref": "contracts/deliverable-requirements.json",
        "artifact_revision": "professional-deliverable-requirements-v1",
        "artifact_sha256": "..."
      }
    }

The loader deliberately reads only the profile identity, writer state root,
and this pointer. It does not own the complete project-profile schema. Callers
can therefore adopt the contract layer without forking the control plane or
relaxing another profile validator.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from pipeline.validate_json_suites import SuiteConfigurationError, SubsetValidator


PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
REVISION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
CONTRACT_ID_RE = re.compile(r"^[a-z][a-z0-9-]+@[0-9]+$")
FIELD_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
CHECK_ID_RE = FIELD_ID_RE
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

SUPPORTED_PAYLOAD_TYPES = {
    "array",
    "boolean",
    "integer",
    "number",
    "object",
    "string",
}


class ProfessionalContractError(ValueError):
    """Raised when a pinned contract or professional deliverable fails closed."""


@dataclass(frozen=True)
class PinnedContractFile:
    ref: str
    revision: str
    sha256: str
    path: Path
    content: bytes
    value: dict[str, Any]


@dataclass(frozen=True)
class DeliverableRequirement:
    contract_id: str
    description: str
    required_payload_fields: dict[str, str]
    required_quality_checks: tuple[str, ...]


@dataclass(frozen=True)
class ProfessionalContractBundle:
    workspace: Path
    project_profile_path: Path
    artifact_root: Path
    project_id: str
    project_profile_revision: str
    schema: PinnedContractFile
    requirements: PinnedContractFile
    validator: SubsetValidator
    contracts: dict[str, DeliverableRequirement]


@dataclass(frozen=True)
class ValidatedDeliverable:
    """Validated value plus the hash of the exact input bytes."""

    content: bytes
    sha256: str
    value: dict[str, Any]
    requirement: DeliverableRequirement


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _strict_json(content: bytes, label: str) -> Any:
    if not isinstance(content, bytes):
        raise ProfessionalContractError(f"{label} must be exact bytes")

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-JSON numeric constant {value}")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(
            content.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ProfessionalContractError(
            f"{label} is not valid JSON: line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ) from exc
    except (UnicodeError, ValueError) as exc:
        raise ProfessionalContractError(
            f"{label} is not strict UTF-8 JSON: {exc}"
        ) from exc


def _json_object(content: bytes, label: str) -> dict[str, Any]:
    value = _strict_json(content, label)
    if not isinstance(value, dict):
        raise ProfessionalContractError(f"{label} must be a JSON object")
    return value


def _workspace(path: Path) -> Path:
    try:
        resolved = Path(path).resolve(strict=True)
    except OSError as exc:
        raise ProfessionalContractError(f"workspace is unavailable: {path}") from exc
    if not resolved.is_dir():
        raise ProfessionalContractError(f"workspace is not a directory: {resolved}")
    return resolved


def _resolve_relative_file(root: Path, raw_ref: Any, label: str) -> Path:
    if not isinstance(raw_ref, str) or not raw_ref:
        raise ProfessionalContractError(f"{label} must be a non-empty relative path")
    if "\\" in raw_ref or "\x00" in raw_ref:
        raise ProfessionalContractError(f"{label} contains forbidden characters")
    parts = raw_ref.split("/")
    if raw_ref.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        raise ProfessionalContractError(f"{label} escapes its allowed root")

    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ProfessionalContractError(f"{label} may not traverse a symlink")
    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ProfessionalContractError(
            f"{label} is missing or escapes its allowed root"
        ) from exc
    if not resolved.is_file():
        raise ProfessionalContractError(f"{label} is not a regular file")
    return resolved


def _resolve_relative_root(workspace: Path, raw_ref: Any, label: str) -> Path:
    if not isinstance(raw_ref, str) or not raw_ref:
        raise ProfessionalContractError(f"{label} must be a non-empty relative path")
    if "\\" in raw_ref or "\x00" in raw_ref:
        raise ProfessionalContractError(f"{label} contains forbidden characters")
    parts = raw_ref.split("/")
    if raw_ref.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        raise ProfessionalContractError(f"{label} escapes its allowed root")
    current = workspace
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ProfessionalContractError(f"{label} may not traverse a symlink")
    try:
        current.relative_to(workspace)
    except ValueError as exc:
        raise ProfessionalContractError(f"{label} escapes its allowed root") from exc
    return current


def _resolve_profile(workspace: Path, profile_path: Path) -> Path:
    supplied = Path(profile_path)
    candidate = supplied if supplied.is_absolute() else workspace / supplied
    if candidate.is_symlink():
        raise ProfessionalContractError("project profile may not be a symlink")
    try:
        resolved_candidate = candidate.resolve(strict=True)
        relative = resolved_candidate.relative_to(workspace)
    except (OSError, ValueError) as exc:
        raise ProfessionalContractError("project profile escapes the workspace") from exc
    return _resolve_relative_file(workspace, relative.as_posix(), "project profile")


def _pointer(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ProfessionalContractError(f"{label} must be an artifact pointer")
    expected = {"artifact_ref", "artifact_revision", "artifact_sha256"}
    if set(value) != expected:
        raise ProfessionalContractError(f"{label} has unexpected fields")
    revision = value["artifact_revision"]
    sha256 = value["artifact_sha256"]
    if not isinstance(revision, str) or not REVISION_RE.fullmatch(revision):
        raise ProfessionalContractError(f"{label}.artifact_revision is invalid")
    if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
        raise ProfessionalContractError(f"{label}.artifact_sha256 is invalid")
    return {
        "artifact_ref": value["artifact_ref"],
        "artifact_revision": revision,
        "artifact_sha256": sha256,
    }


def _load_pinned(
    workspace: Path, pointer: dict[str, str], label: str
) -> PinnedContractFile:
    path = _resolve_relative_file(
        workspace, pointer["artifact_ref"], f"{label}.artifact_ref"
    )
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ProfessionalContractError(f"cannot read {label}: {path}") from exc
    actual_hash = _sha256(content)
    if actual_hash != pointer["artifact_sha256"]:
        raise ProfessionalContractError(
            f"{label} hash mismatch: expected {pointer['artifact_sha256']}, "
            f"got {actual_hash}"
        )
    return PinnedContractFile(
        ref=pointer["artifact_ref"],
        revision=pointer["artifact_revision"],
        sha256=actual_hash,
        path=path,
        content=content,
        value=_json_object(content, label),
    )


def _requirements(
    artifact: PinnedContractFile, schema_contract: str
) -> dict[str, DeliverableRequirement]:
    value = artifact.value
    if set(value) != {
        "requirements_version",
        "requirements_revision",
        "schema_contract",
        "contracts",
    }:
        raise ProfessionalContractError("deliverable requirements have unexpected fields")
    if value["requirements_version"] != "1.0":
        raise ProfessionalContractError("unsupported deliverable requirements version")
    if value["requirements_revision"] != artifact.revision:
        raise ProfessionalContractError(
            "deliverable requirements revision does not match its pointer"
        )
    if value["schema_contract"] != schema_contract:
        raise ProfessionalContractError(
            "deliverable requirements do not target the pinned schema contract"
        )

    raw_contracts = value["contracts"]
    if not isinstance(raw_contracts, dict) or not raw_contracts:
        raise ProfessionalContractError(
            "deliverable requirements contracts must be a non-empty object"
        )

    contracts: dict[str, DeliverableRequirement] = {}
    for contract_id, raw in raw_contracts.items():
        if not isinstance(contract_id, str) or not CONTRACT_ID_RE.fullmatch(contract_id):
            raise ProfessionalContractError(
                f"invalid deliverable contract identifier: {contract_id!r}"
            )
        if not isinstance(raw, dict) or set(raw) != {
            "description",
            "required_payload_fields",
            "required_quality_checks",
        }:
            raise ProfessionalContractError(
                f"requirements for {contract_id} have unexpected fields"
            )
        description = raw["description"]
        if not isinstance(description, str) or not description.strip():
            raise ProfessionalContractError(
                f"requirements for {contract_id} need a description"
            )

        raw_fields = raw["required_payload_fields"]
        if not isinstance(raw_fields, dict) or not raw_fields:
            raise ProfessionalContractError(
                f"required payload fields for {contract_id} must be non-empty"
            )
        fields: dict[str, str] = {}
        for field_name, field_type in raw_fields.items():
            if not isinstance(field_name, str) or not FIELD_ID_RE.fullmatch(field_name):
                raise ProfessionalContractError(
                    f"invalid required payload field for {contract_id}: {field_name!r}"
                )
            if field_type not in SUPPORTED_PAYLOAD_TYPES:
                raise ProfessionalContractError(
                    f"unsupported payload type for {contract_id}.{field_name}: "
                    f"{field_type!r}"
                )
            fields[field_name] = field_type

        raw_checks = raw["required_quality_checks"]
        if not isinstance(raw_checks, list) or not raw_checks:
            raise ProfessionalContractError(
                f"required quality checks for {contract_id} must be non-empty"
            )
        if not all(
            isinstance(check, str) and CHECK_ID_RE.fullmatch(check)
            for check in raw_checks
        ):
            raise ProfessionalContractError(
                f"required quality checks for {contract_id} contain an invalid ID"
            )
        if len(set(raw_checks)) != len(raw_checks):
            raise ProfessionalContractError(
                f"required quality checks for {contract_id} contain duplicates"
            )
        contracts[contract_id] = DeliverableRequirement(
            contract_id=contract_id,
            description=description,
            required_payload_fields=fields,
            required_quality_checks=tuple(raw_checks),
        )
    return contracts


def load_professional_contracts(
    workspace_path: Path, project_profile_path: Path
) -> ProfessionalContractBundle:
    """Load the schema and requirements catalog pinned by one project profile."""

    workspace = _workspace(workspace_path)
    profile_path = _resolve_profile(workspace, project_profile_path)
    try:
        profile_content = profile_path.read_bytes()
    except OSError as exc:
        raise ProfessionalContractError(
            f"cannot read project profile: {profile_path}"
        ) from exc
    profile = _json_object(profile_content, "project profile")

    project_id = profile.get("project_id")
    profile_revision = profile.get("profile_revision")
    if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
        raise ProfessionalContractError("project profile project_id is invalid")
    if (
        not isinstance(profile_revision, str)
        or not REVISION_RE.fullmatch(profile_revision)
    ):
        raise ProfessionalContractError("project profile profile_revision is invalid")
    writer = profile.get("writer")
    if not isinstance(writer, dict) or "state_root" not in writer:
        raise ProfessionalContractError(
            "project profile writer.state_root is required for evidence confinement"
        )
    artifact_root = _resolve_relative_root(
        workspace, writer["state_root"], "project profile writer.state_root"
    )

    raw_binding = profile.get("professional_contracts")
    if not isinstance(raw_binding, dict) or set(raw_binding) != {
        "schema",
        "requirements",
    }:
        raise ProfessionalContractError(
            "project profile professional_contracts must pin schema and requirements"
        )
    schema_pointer = _pointer(raw_binding["schema"], "professional_contracts.schema")
    requirements_pointer = _pointer(
        raw_binding["requirements"], "professional_contracts.requirements"
    )

    schema = _load_pinned(workspace, schema_pointer, "professional schema")
    schema_contract = schema.value.get("$id")
    if schema_contract != "professional-deliverable@1":
        raise ProfessionalContractError(
            "unsupported professional deliverable schema contract"
        )
    try:
        validator = SubsetValidator(schema.value)
    except SuiteConfigurationError as exc:
        raise ProfessionalContractError(
            f"professional deliverable schema is unsupported: {exc}"
        ) from exc

    requirements = _load_pinned(
        workspace, requirements_pointer, "deliverable requirements"
    )
    contracts = _requirements(requirements, schema_contract)
    return ProfessionalContractBundle(
        workspace=workspace,
        project_profile_path=profile_path,
        artifact_root=artifact_root,
        project_id=project_id,
        project_profile_revision=profile_revision,
        schema=schema,
        requirements=requirements,
        validator=validator,
        contracts=contracts,
    )


def _json_type_matches(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    raise ProfessionalContractError(f"unsupported payload type: {expected}")


def _field_is_material(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(value)
    return value is not None


def _verify_evidence_pointer(
    bundle: ProfessionalContractBundle, raw: dict[str, Any], label: str,
    pending_evidence: dict[tuple[str, str, str], bytes] | None = None,
) -> tuple[str, str, str]:
    pointer = _pointer(raw, label)
    identity = (pointer["artifact_ref"], pointer["artifact_revision"], pointer["artifact_sha256"])
    if pending_evidence is not None and identity in pending_evidence:
        # Only exact bytes from the caller's already confined writer transaction.
        # Do not read a fallback file or trust a hash supplied without the bytes.
        _resolve_relative_root(bundle.artifact_root, pointer["artifact_ref"], label)
        if _sha256(pending_evidence[identity]) != pointer["artifact_sha256"]:
            raise ProfessionalContractError(f"{label} pending evidence hash mismatch")
        return identity
    path = _resolve_relative_file(
        bundle.artifact_root, pointer["artifact_ref"], f"{label}.artifact_ref"
    )
    try:
        actual_hash = _sha256(path.read_bytes())
    except OSError as exc:
        raise ProfessionalContractError(f"cannot read evidence for {label}") from exc
    if actual_hash != pointer["artifact_sha256"]:
        raise ProfessionalContractError(
            f"{label} hash mismatch: expected {pointer['artifact_sha256']}, "
            f"got {actual_hash}"
        )
    return (
        pointer["artifact_ref"],
        pointer["artifact_revision"],
        pointer["artifact_sha256"],
    )


def validate_deliverable_bytes(
    bundle: ProfessionalContractBundle,
    content: bytes,
    *,
    expected_run_id: str,
    expected_task_id: str,
    expected_task_sha256: str,
    expected_step_id: str,
    expected_role_id: str,
    expected_contract_id: str,
    expected_artifact_revision: str,
    pending_evidence: dict[tuple[str, str, str], bytes] | None = None,
) -> ValidatedDeliverable:
    """Validate exact deliverable bytes against profile and task identity.

    Validation is fail-closed: strict UTF-8 JSON, common schema, selected project
    identity, caller-supplied task/role/contract identity, concrete payload field
    types, every required quality check at ``pass``, and hash-valid evidence files.
    """

    value = _json_object(content, "professional deliverable")
    schema_errors = bundle.validator.validate(value)
    if schema_errors:
        details = "; ".join(
            f"{error.instance_path} {error.rule}: {error.message}"
            for error in schema_errors
        )
        raise ProfessionalContractError(
            f"professional deliverable schema validation failed: {details}"
        )

    expected_identity = {
        "project_id": bundle.project_id,
        "project_profile_revision": bundle.project_profile_revision,
        "run_id": expected_run_id,
        "task_id": expected_task_id,
        "task_sha256": expected_task_sha256,
        "step_id": expected_step_id,
        "role_id": expected_role_id,
        "contract_id": expected_contract_id,
        "artifact_revision": expected_artifact_revision,
    }
    for field, expected in expected_identity.items():
        if value[field] != expected:
            raise ProfessionalContractError(
                f"professional deliverable {field} does not match expected identity"
            )

    requirement = bundle.contracts.get(expected_contract_id)
    if requirement is None:
        raise ProfessionalContractError(
            f"unregistered professional deliverable contract: {expected_contract_id}"
        )

    payload = value["payload"]
    for field, expected_type in requirement.required_payload_fields.items():
        if field not in payload:
            raise ProfessionalContractError(
                f"{expected_contract_id} payload is missing required field {field!r}"
            )
        field_value = payload[field]
        if not _json_type_matches(field_value, expected_type):
            raise ProfessionalContractError(
                f"{expected_contract_id} payload field {field!r} must be "
                f"{expected_type}"
            )
        if not _field_is_material(field_value):
            raise ProfessionalContractError(
                f"{expected_contract_id} payload field {field!r} is empty"
            )

    checks_by_id: dict[str, dict[str, Any]] = {}
    for check in value["quality_checks"]:
        check_id = check["check_id"]
        if check_id in checks_by_id:
            raise ProfessionalContractError(
                f"professional deliverable contains duplicate quality check {check_id!r}"
            )
        checks_by_id[check_id] = check
        if check["status"] != "pass":
            raise ProfessionalContractError(
                f"quality check {check_id!r} is not pass"
            )
    missing_checks = [
        check
        for check in requirement.required_quality_checks
        if check not in checks_by_id
    ]
    if missing_checks:
        raise ProfessionalContractError(
            f"{expected_contract_id} is missing required quality checks: "
            + ", ".join(missing_checks)
        )

    declared_evidence: set[tuple[str, str, str]] = set()
    for index, evidence in enumerate(value["evidence_refs"]):
        declared_evidence.add(
            _verify_evidence_pointer(
                bundle, evidence, f"evidence_refs[{index}]", pending_evidence
            )
        )
    for check_index, check in enumerate(value["quality_checks"]):
        for evidence_index, evidence in enumerate(check["evidence_refs"]):
            pointer = _verify_evidence_pointer(
                bundle,
                evidence,
                f"quality_checks[{check_index}].evidence_refs[{evidence_index}]",
                pending_evidence,
            )
            if pointer not in declared_evidence:
                raise ProfessionalContractError(
                    f"quality check {check['check_id']!r} uses undeclared evidence"
                )

    return ValidatedDeliverable(
        content=content,
        sha256=_sha256(content),
        value=value,
        requirement=requirement,
    )
