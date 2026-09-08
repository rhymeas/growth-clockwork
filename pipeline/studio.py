"""Local planning and inert material intake; no network, model or publication.

All records are immutable Root Writer events. Uploaded bytes remain base64 JSON,
never executable files. Text extraction is deterministic, not a claim verifier.
"""
from __future__ import annotations

import base64
import binascii
import csv
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import tempfile
import threading
from typing import Any
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pipeline import media_processor, root_writer

# Audience research and native platform analytics intentionally cover only the
# five social networks. Studio planning also includes the two editorial homes.
PLATFORMS = ("youtube", "instagram", "tiktok", "x", "pinterest")
CONTENT_CHANNELS = ("website", "medium", *PLATFORMS)
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_LEGACY_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_REQUEST_BYTES = 3 * 1024 * 1024
MAX_MATERIAL_STORAGE_BYTES = 512 * 1024 * 1024
ROOT = PurePosixPath("records/studio")
BLOB_ROOT = PurePosixPath("records/material-blobs")
NAMESPACE = uuid.UUID("da8b9f67-ae7d-42ae-bbae-f5f6e00eaeae")
# One local API process owns Studio. Serialize validation + append + readback,
# not just individual Root Writer commits, so concurrent uploads cannot bypass
# deduplication/quotas or race planning cancellation. Reentrant for readback.
_STUDIO_LOCK = threading.RLock()
TEXT_TYPES = {".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv", ".json": "application/json"}
MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}


class StudioError(ValueError):
    def __init__(self, message: str, status: int = 400, code: str = "invalid_studio_request"):
        super().__init__(message)
        self.status, self.code = status, code


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _keys(value: Any, expected: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise StudioError("Request fields are incomplete or unsupported")


def _text(value: Any, label: str, limit: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise StudioError(f"{label} must be non-empty text, at most {limit} characters")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise StudioError(f"{label} is not valid UTF-8") from exc
    return value.strip()


def _request(profile: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    content = _json(record)
    return {"writer_request_version": root_writer.REQUEST_VERSION,
        "project_id": profile["project_id"], "project_profile_revision": profile["profile_revision"],
        "run_id": f"studio-{record['id']}", "idempotency_key": record["id"],
        "requested_by": profile["root_role_id"], "writes": [{
            "path": str(ROOT / f"{record['id']}.json"), "mode": "create",
            "content": content.decode("utf-8"), "content_sha256": _hash(content),
            "expected_sha256": None, "media_type": "application/json"}]}


def _record_receipt(record: dict[str, Any]) -> dict[str, Any]:
    """Safe pointer to the exact immutable Studio input saved by Root Writer."""
    return {
        "id": record["id"],
        "kind": record["kind"],
        "artifact_ref": str(ROOT / f"{record['id']}.json"),
        "artifact_sha256": _hash(_json(record)),
    }


def _records(workspace: Path, profile: dict[str, Any]) -> list[dict[str, Any]]:
    directory = root_writer._assert_no_symlink_path(workspace, profile["_state_root"] / ROOT)
    if not directory.exists():
        return []
    paths = sorted(directory.iterdir())
    if len(paths) > 500:
        raise StudioError("Studio record limit reached; archive support is required", 409)
    state_fd = os.open(workspace.joinpath(*profile["_state_root"].parts), root_writer._directory_flags())
    result = []
    total_record_bytes = 0
    try:
        for path in paths:
            if not re.fullmatch(r"[0-9a-f-]{36}\.json", path.name):
                raise StudioError("Studio state contains an unsupported record", 409)
            safe = root_writer._assert_no_symlink_path(workspace, profile["_state_root"] / ROOT / path.name)
            if not safe.is_file() or safe.stat().st_size > MAX_UPLOAD_REQUEST_BYTES + 100_000:
                raise StudioError("Studio record size is invalid", 409)
            total_record_bytes += safe.stat().st_size
            if total_record_bytes > 32 * 1024 * 1024:
                raise StudioError("Studio state exceeds its local reading limit", 409)
            content = root_writer._read_relative_bytes(state_fd, ROOT / path.name)
            record = json.loads(content)
            if (record.get("project_id") != profile["project_id"] or
                record.get("project_profile_revision") != profile["profile_revision"] or
                path.name != f"{record.get('id')}.json"):
                raise StudioError("Studio record identity cannot be verified", 409)
            request = _request(profile, record)
            receipt = root_writer._idempotent_receipt(root_writer._receipt_relative(profile, request), _hash(_json(request)), state_fd, request)
            if receipt is None:
                raise StudioError("Studio record lacks a completed write receipt", 409)
            result.append(record)
    finally:
        os.close(state_fd)
    return sorted(result, key=lambda record: (record["created_at"], record["id"]))


def _view(workspace: Path, profile: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {"project_id": profile["project_id"], "project_profile_revision": profile["profile_revision"],
        "audiences": [], "proposals": [], "slots": [], "materials": [], "suggestions": [],
        "capabilities": {"platforms": list(CONTENT_CHANNELS), "publishing": False, "platform_data": False, "max_upload_bytes": MAX_UPLOAD_BYTES,
            "max_material_storage_bytes": MAX_MATERIAL_STORAGE_BYTES,
            "media_processing": media_processor.capabilities(workspace)}}
    buckets = {"audience": "audiences", "proposal": "proposals", "slot": "slots", "material": "materials"}
    slots: dict[str, dict[str, Any]] = {}
    for record in records:
        if record["kind"] == "cancel_slot":
            slots = {key: slot for key, slot in slots.items() if slot["id"] != record["payload"]["slot_id"]}
            continue
        if record["kind"] not in buckets:
            continue
        payload = {key: val for key, val in record["payload"].items()
                   if key not in {"content_base64", "blob_ref"}}
        item = {**payload, "id": record["id"], "created_at": record["created_at"]}
        if record["kind"] == "slot":
            slots[item["proposal_id"]] = item
        else:
            value[buckets[record["kind"]]].append(item)
    value["slots"] = sorted(slots.values(), key=lambda slot: slot["planned_at"])
    formats = {
        "website": "Complete website article",
        "medium": "Native Medium story",
        "youtube": "Worked-example video",
        "instagram": "Step-by-step carousel",
        "tiktok": "One-method short video",
        "x": "Self-contained explanation",
        "pinterest": "Visual reference guide",
    }
    for material in value["materials"][-10:]:
        for channel, label in formats.items():
            value["suggestions"].append({"id": f"{material['id']}-{channel}", "title": f"{label}: {material['name']}", "channel": channel,
                "material_ids": [material["id"]], "basis": "format_suggestion",
                "detail": "Format starter from this material. Audience fit, claims and source rights still need checking."})
    return value


def read(workspace: Path, profile: dict[str, Any]) -> dict[str, Any]:
    with _STUDIO_LOCK:
        return _read(workspace, profile)


def _read(workspace: Path, profile: dict[str, Any]) -> dict[str, Any]:
    try:
        return _view(workspace, profile, _records(workspace, profile))
    except (root_writer.WriterError, OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        if isinstance(exc, StudioError):
            raise
        raise StudioError("Studio state could not be verified", 409, "invalid_studio_state") from exc


def material_asset(workspace: Path, profile: dict[str, Any], material_id: str) -> dict[str, Any]:
    """Return one receipt-verified material and its exact decoded bytes.

    This is an internal boundary for review and publisher adapters.  It never
    writes the decoded bytes to disk and never trusts a caller-supplied name,
    MIME type, size, or digest.
    """
    try:
        if str(uuid.UUID(material_id)) != material_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise StudioError("Material ID is invalid", 400) from exc
    with _STUDIO_LOCK:
        record = next(
            (item for item in _records(workspace, profile)
             if item.get("id") == material_id and item.get("kind") == "material"),
            None,
        )
    if record is None:
        raise StudioError("Material is unavailable", 404, "material_not_found")
    payload = record.get("payload")
    common = {
        "name", "mime_type", "size_bytes", "sha256", "content_base64",
        "processing_status", "processing_note", "extracted_text", "metadata",
    }
    if not isinstance(payload, dict):
        raise StudioError("Material record is invalid", 409, "invalid_studio_state")
    if set(payload) == common:
        try:
            content = base64.b64decode(payload["content_base64"], validate=True)
        except (ValueError, TypeError, binascii.Error) as exc:
            raise StudioError("Material bytes cannot be verified", 409, "invalid_studio_state") from exc
    elif set(payload) == (common - {"content_base64"}) | {"blob_ref"}:
        expected_ref = BLOB_ROOT / f"{payload.get('sha256')}.blob"
        if payload.get("blob_ref") != expected_ref.as_posix():
            raise StudioError("Material blob identity is invalid", 409, "invalid_studio_state")
        try:
            state_fd = os.open(
                workspace.joinpath(*profile["_state_root"].parts),
                root_writer._directory_flags(),
            )
            try:
                content = root_writer._read_relative_bytes(state_fd, expected_ref)
            finally:
                os.close(state_fd)
        except (OSError, root_writer.WriterError) as exc:
            raise StudioError("Material blob cannot be verified", 409, "invalid_studio_state") from exc
        assert content is not None
    else:
        raise StudioError("Material record is invalid", 409, "invalid_studio_state")
    allowed_mime = set(TEXT_TYPES.values()) | set(MEDIA_TYPES.values())
    if (
        not isinstance(payload["name"], str)
        or not payload["name"]
        or payload["mime_type"] not in allowed_mime
        or not isinstance(payload["size_bytes"], int)
        or isinstance(payload["size_bytes"], bool)
        or payload["size_bytes"] != len(content)
        or not isinstance(payload["sha256"], str)
        or payload["sha256"] != _hash(content)
        or not 0 < len(content) <= MAX_UPLOAD_BYTES
    ):
        raise StudioError("Material bytes do not match their immutable record", 409, "invalid_studio_state")
    record_bytes = _json(record)
    return {
        "material_id": material_id,
        "record_ref": str(ROOT / f"{material_id}.json"),
        "record_sha256": _hash(record_bytes),
        "name": payload["name"],
        "mime_type": payload["mime_type"],
        "size_bytes": payload["size_bytes"],
        "content_sha256": payload["sha256"],
        "processing_status": payload["processing_status"],
        "content": content,
    }


def _material_bytes(workspace: Path, name_value: Any, mime_value: Any, content: bytes) -> dict[str, Any]:
    name = _text(name_value, "Filename", 180)
    if any(char in name for char in ("/", "\\", "\r", "\n")) or name in {".", ".."} or any(ord(c) < 32 for c in name):
        raise StudioError("Filename must not contain a path or control characters")
    suffix = Path(name).suffix.lower()
    expected = {**TEXT_TYPES, **MEDIA_TYPES}.get(suffix)
    if expected is None or not isinstance(mime_value, str) or mime_value not in {expected, ""}:
        raise StudioError("This file type is unsupported or its MIME type does not match")
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise StudioError("Material must contain 1 byte to 64 MiB", 413)
    metadata: dict[str, Any] = {}
    extracted = ""
    status, note = "indexed", "Stored and fingerprinted locally. No transcription, OCR or media decoding has run."
    if suffix in TEXT_TYPES:
        try:
            extracted = content.decode("utf-8-sig")
        except UnicodeError as exc:
            raise StudioError("Text material must use UTF-8") from exc
        if "\x00" in extracted:
            raise StudioError("Binary content is not accepted as text")
        metadata = {"characters": len(extracted), "lines": len(extracted.splitlines()), "words": len(extracted.split())}
        if suffix == ".json":
            try:
                parsed = json.loads(extracted, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError, RecursionError) as exc:
                raise StudioError("JSON material is malformed") from exc
            metadata["json_type"] = type(parsed).__name__
        elif suffix == ".csv":
            try:
                rows = list(csv.reader(io.StringIO(extracted)))
                metadata.update(rows=len(rows), columns=len(rows[0]) if rows else 0)
            except csv.Error as exc:
                raise StudioError("CSV material is malformed") from exc
        metadata["preview_truncated"] = len(extracted) > 12000
        extracted = extracted[:12000]
        status, note = "processed", "UTF-8 text extracted and counted locally. Content remains untrusted; claims are not verified."
    else:
        valid = False
        if suffix == ".png":
            valid = len(content) >= 33 and content[:8] == b"\x89PNG\r\n\x1a\n" and content[12:16] == b"IHDR"
            if valid:
                metadata["width"], metadata["height"] = struct.unpack(">II", content[16:24])
        elif suffix in {".jpg", ".jpeg"}:
            valid = content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9")
        elif suffix == ".webp":
            valid = len(content) >= 16 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
        elif suffix == ".wav":
            valid = len(content) >= 16 and content[:4] == b"RIFF" and content[8:12] == b"WAVE"
        elif suffix == ".mp3":
            valid = content.startswith(b"ID3") or (len(content) > 2 and content[0] == 255 and content[1] & 224 == 224)
        elif suffix in {".mp4", ".mov"}:
            valid = len(content) >= 16 and content[4:8] == b"ftyp"
        elif suffix == ".webm":
            valid = content.startswith(b"\x1aE\xdf\xa3")
        if not valid:
            raise StudioError("File signature does not match its declared type")
        processing = media_processor.process(workspace, suffix=suffix, content=content)
        metadata.update(processing["metadata"])
        extracted = processing["extracted_text"]
        status, note = processing["status"], processing["note"]
    return {"name": name, "mime_type": expected, "size_bytes": len(content), "sha256": _hash(content),
        "processing_status": status,
        "processing_note": note, "extracted_text": extracted, "metadata": metadata}


def _material(workspace: Path, value: dict[str, Any]) -> dict[str, Any]:
    encoded = value["content_base64"]
    if not isinstance(encoded, str) or len(encoded) > ((MAX_LEGACY_UPLOAD_BYTES + 2) // 3) * 4:
        raise StudioError("Legacy material upload exceeds the 2 MiB limit", 413)
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise StudioError("Material is not valid base64") from exc
    if not content or len(content) > MAX_LEGACY_UPLOAD_BYTES:
        raise StudioError("Legacy material upload must contain 1 byte to 2 MiB", 413)
    payload = _material_bytes(workspace, value["name"], value["mime_type"], content)
    payload["content_base64"] = base64.b64encode(content).decode("ascii")
    return payload


def _store_blob(workspace: Path, profile_path: Path, profile: dict[str, Any], content: bytes) -> str:
    """Store one immutable content-addressed blob with Root Writer's POSIX primitives.

    A crash before the Studio record can leave an unreferenced blob. That blob is
    inert and safely reused by its exact digest on retry; records never point to a
    missing or unverified blob.
    """
    digest = _hash(content)
    relative = BLOB_ROOT / f"{digest}.blob"
    if not any(root_writer._under_prefix(relative, prefix) for prefix in profile["_allowed_roots"]):
        raise StudioError("Project profile does not allow material blobs", 409)
    if not any(root_writer._under_prefix(relative, prefix) for prefix in profile["_append_only_roots"]):
        raise StudioError("Material blob path must be append-only", 409)
    checked_workspace = root_writer._validate_workspace(workspace)
    root_writer._validate_profile_package(checked_workspace, profile_path, profile)
    with root_writer._workspace_lock(checked_workspace):
        with root_writer._project_state_fd(checked_workspace, profile["_state_root"]) as state_fd:
            transaction_fd = root_writer._open_directory_chain(
                state_fd, (".writer-tx",), create=True,
                label="writer transaction directory",
            )
            try:
                os.fchmod(transaction_fd, 0o700)
                root_writer._cleanup_transaction_stages(transaction_fd)
                existing = root_writer._read_relative_bytes(state_fd, relative, missing_ok=True)
                if existing is None:
                    root_writer._publish_exclusive(state_fd, transaction_fd, relative, content)
                elif existing != content:
                    raise StudioError("Existing material blob differs from its content hash", 409)
                readback = root_writer._read_relative_bytes(state_fd, relative)
                if readback is None or len(readback) != len(content) or _hash(readback) != digest:
                    raise StudioError("Material blob readback failed", 409)
            finally:
                os.close(transaction_fd)
    return relative.as_posix()


def write(workspace: Path, profile_path: Path, profile: dict[str, Any], value: Any, *, material: bool = False) -> dict[str, Any]:
    with _STUDIO_LOCK:
        return _write(workspace, profile_path, profile, value, material=material)


def write_binary(workspace: Path, profile_path: Path, profile: dict[str, Any], value: Any, content: bytes) -> dict[str, Any]:
    with _STUDIO_LOCK:
        return _write(workspace, profile_path, profile, value, material=True, raw_content=content)


def _write(workspace: Path, profile_path: Path, profile: dict[str, Any], value: Any, *, material: bool = False,
           raw_content: bytes | None = None) -> dict[str, Any]:
    fields = {"project_id", "project_profile_revision", "request_id"}
    material_fields = {"name", "mime_type"} | ({"content_base64"} if raw_content is None else set())
    _keys(value, fields | (material_fields if material else {"kind", "payload"}))
    if value["project_id"] != profile["project_id"] or value["project_profile_revision"] != profile["profile_revision"]:
        raise StudioError("The selected project profile changed; refresh before saving", 409)
    try:
        if str(uuid.UUID(value["request_id"])) != value["request_id"]:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise StudioError("request_id must be a canonical UUID") from exc
    identifier = str(uuid.uuid5(NAMESPACE, f"{profile['project_id']}:{profile['profile_revision']}:{value['request_id']}"))
    try:
        fingerprint_value = value if raw_content is None else {**value, "content_sha256": _hash(raw_content)}
        fingerprint = _hash(_json(fingerprint_value))
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise StudioError("Request must contain bounded valid UTF-8 JSON") from exc
    try:
        records = _records(workspace, profile)
        existing = next((record for record in records if record["id"] == identifier), None)
        if existing:
            if existing["request_sha256"] != fingerprint:
                raise StudioError("This request ID already saved different content", 409)
            result = {"studio": _view(workspace, profile, records), "created": False,
                "record": _record_receipt(existing)}
            if material:
                result["material_id"] = existing["payload"]["material_id"] if existing["kind"] == "material_reuse" else existing["id"]
            return result
        if len(records) >= 500:
            raise StudioError("Studio record limit reached", 409)
        current = _view(workspace, profile, records)
        kind = "material" if material else value["kind"]
        if kind == "material" and material:
            payload = (_material(workspace, value) if raw_content is None else
                       _material_bytes(workspace, value["name"], value["mime_type"], raw_content))
            duplicate = next((item for item in current["materials"] if item["sha256"] == payload["sha256"]), None)
            if duplicate:
                kind, payload = "material_reuse", {"material_id": duplicate["id"]}
            elif sum(item["size_bytes"] for item in current["materials"]) + payload["size_bytes"] > MAX_MATERIAL_STORAGE_BYTES:
                raise StudioError("This local project has reached its 512 MiB material limit", 413)
            elif raw_content is not None:
                payload["blob_ref"] = _store_blob(workspace, profile_path, profile, raw_content)
        elif kind == "audience":
            _keys(value["payload"], {"label", "problem"})
            payload = {"label": _text(value["payload"]["label"], "Audience", 200), "problem": _text(value["payload"]["problem"], "Problem"), "status": "hypothesis"}
        elif kind == "proposal":
            raw = value["payload"]
            _keys(raw, {"title", "channel", "audience_id", "material_ids"})
            if raw["channel"] not in CONTENT_CHANNELS:
                raise StudioError("Choose a supported channel")
            if raw["audience_id"] is not None and _text(raw["audience_id"], "Audience ID", 36) not in {item["id"] for item in current["audiences"]}:
                raise StudioError("Audience does not belong to this project", 409)
            ids = raw["material_ids"]
            if not isinstance(ids, list) or len(ids) > 10 or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
                raise StudioError("Select at most ten unique materials")
            if not set(ids).issubset({item["id"] for item in current["materials"]}):
                raise StudioError("Material does not belong to this project", 409)
            payload = {**raw, "title": _text(raw["title"], "Title", 240), "status": "draft"}
        elif kind == "cancel_slot":
            _keys(value["payload"], {"slot_id"})
            if _text(value["payload"]["slot_id"], "Slot ID", 36) not in {item["id"] for item in current["slots"]}:
                raise StudioError("This planning slot is no longer active", 409)
            payload = {"slot_id": value["payload"]["slot_id"]}
        elif kind == "slot":
            raw = value["payload"]
            _keys(raw, {"proposal_id", "planned_at", "timezone"})
            if _text(raw["proposal_id"], "Proposal ID", 36) not in {item["id"] for item in current["proposals"]}:
                raise StudioError("Proposal does not belong to this project", 409)
            try:
                moment = dt.datetime.fromisoformat(_text(raw["planned_at"], "Planning time", 40).replace("Z", "+00:00"))
                if moment.tzinfo is None or not 2000 <= moment.year <= 2100:
                    raise ValueError()
                ZoneInfo(_text(raw["timezone"], "Timezone", 120))
            except (ValueError, TypeError, AttributeError, ZoneInfoNotFoundError) as exc:
                raise StudioError("Planning requires a real timestamp with offset and IANA timezone") from exc
            payload = {"proposal_id": raw["proposal_id"], "planned_at": moment.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"), "timezone": raw["timezone"], "status": "planned"}
        else:
            raise StudioError("Unsupported studio action")
        record = {"version": "1.0", "id": identifier, "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"], "request_sha256": fingerprint,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "kind": kind, "payload": payload}
        with tempfile.NamedTemporaryFile(prefix="growth-studio-", suffix=".json") as handle:
            handle.write(_json(_request(profile, record)))
            handle.flush()
            root_writer.apply_request(workspace, profile_path, Path(handle.name))
        result = {"studio": read(workspace, profile), "created": kind != "material_reuse",
            "record": _record_receipt(record)}
        if material:
            result["material_id"] = payload["material_id"] if kind == "material_reuse" else identifier
        return result
    except (root_writer.WriterError, OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        if isinstance(exc, StudioError):
            raise
        raise StudioError("Studio change could not be safely saved", 409, "studio_write_failed") from exc
