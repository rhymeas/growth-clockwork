#!/usr/bin/env python3
"""Permission-gated Postiz delivery for one exact approved release package.

The connector is never started by the local API. It records an immutable intent
before the external request. An intent without a receipt is not retried blindly:
Postiz does not expose a reliable idempotency key for post creation.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable
from urllib import error, request
from urllib.parse import urlsplit
import uuid

from pipeline import release_core


PLATFORMS = frozenset({"youtube", "instagram", "tiktok", "x", "pinterest", "medium"})
PLATFORM_PROVIDERS = {
    "youtube": frozenset({"youtube"}),
    "instagram": frozenset({"instagram", "instagram-standalone"}),
    "tiktok": frozenset({"tiktok"}),
    "x": frozenset({"x"}),
    "pinterest": frozenset({"pinterest"}),
    "medium": frozenset({"medium"}),
}
OPERATIONS = frozenset({"draft", "schedule", "now"})
RFC3339_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
INTENT_NAMESPACE = uuid.UUID("044f5f85-3cbb-4b87-a037-75be6b45fb5d")
MAX_CONFIG_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
POSTIZ_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "video/mp4"})


class PostizConnectorError(ValueError):
    pass


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _contains_credential_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            (isinstance(key, str) and any(
                word in key.lower() for word in ("token", "secret", "password", "api_key")
            )) or _contains_credential_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_credential_key(child) for child in value)
    return False


def _read_json(path: Path, label: str, max_bytes: int = MAX_CONFIG_BYTES) -> dict[str, Any]:
    try:
        info = path.stat()
        if (path.is_symlink() or not path.is_file() or info.st_size > max_bytes
                or (os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077)):
            raise ValueError()
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, UnicodeError, ValueError, TypeError):
        raise PostizConnectorError(f"{label} is missing, invalid or unsafe") from None


def _configuration(workspace: Path, platform: str, operation: str) -> dict[str, Any]:
    permissions = _read_json(workspace / "runtime/permissions.json", "runtime permissions")
    config = _read_json(workspace / "runtime/postiz.json", "Postiz configuration")
    if platform not in PLATFORMS or operation not in OPERATIONS:
        raise PostizConnectorError("unsupported platform or operation")
    try:
        publisher = permissions["agents"]["publisher"]
        connector = permissions["connectors"]["postiz"]
        publish_mode = permissions["publish"]
        integration = config["integrations"][platform]
        base_url = config["base_url"]
        provider = integration["provider"]
        integration_id = integration["id"]
        settings = integration["settings"]
    except (KeyError, TypeError):
        raise PostizConnectorError("Postiz configuration is incomplete") from None
    if publisher.get("enabled") is not True or connector.get("enabled") is not True:
        raise PostizConnectorError("Postiz publisher is not enabled")
    if publish_mode not in {"off", "review", "automatic"}:
        raise PostizConnectorError("runtime publish mode is invalid")
    if publish_mode == "off":
        raise PostizConnectorError("publishing is off")
    connector_mode = connector.get("mode")
    allowed = {
        "drafts-only": {"draft"},
        "schedule": {"draft", "schedule"},
        "automatic": OPERATIONS,
    }.get(connector_mode)
    if allowed is None or operation not in allowed:
        raise PostizConnectorError("operation is not enabled for the Postiz connector")
    if operation != "draft" and publish_mode != "automatic":
        raise PostizConnectorError("schedule and immediate publication require automatic mode")
    if not isinstance(base_url, str) or len(base_url) > 500:
        raise PostizConnectorError("Postiz base URL is invalid")
    parsed = urlsplit(base_url)
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if (parsed.username or parsed.password or parsed.query or parsed.fragment
            or not parsed.hostname or not parsed.path.rstrip("/").endswith("/public/v1")
            or parsed.scheme not in {"http", "https"}
            or (parsed.scheme == "http" and not loopback)):
        raise PostizConnectorError("Postiz base URL must be HTTPS or loopback HTTP and end in /public/v1")
    if (not isinstance(integration_id, str) or not integration_id.strip()
            or integration_id == "replace-me" or len(integration_id) > 256
            or integration.get("enabled") is not True
            or provider not in PLATFORM_PROVIDERS[platform]
            or not isinstance(settings, dict) or settings.get("__type") != provider):
        raise PostizConnectorError("Postiz integration is invalid")
    if _contains_credential_key(settings):
        raise PostizConnectorError("Postiz settings must not contain credentials")
    return {
        "base_url": base_url.rstrip("/"),
        "integration_id": integration_id,
        "provider": provider,
        "settings": settings,
        "publish_mode": publish_mode,
        "connector_mode": connector_mode,
    }


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _post_json(url: str, api_key: str, payload: bytes) -> Any:
    outbound = request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Authorization": api_key, "Content-Type": "application/json"},
    )
    try:
        with request.build_opener(_NoRedirect).open(outbound, timeout=20) as response:
            content = response.read(MAX_RESPONSE_BYTES + 1)
            if len(content) > MAX_RESPONSE_BYTES:
                raise PostizConnectorError("Postiz response is too large")
            return json.loads(content)
    except (error.HTTPError, error.URLError, TimeoutError, OSError, ValueError):
        raise PostizConnectorError("Postiz request outcome requires reconciliation") from None


def _post_media(url: str, api_key: str, name: str, mime_type: str, content: bytes) -> Any:
    boundary = f"growth-clockwork-{uuid.uuid4().hex}"
    safe_name = name.replace('"', "").replace("\r", "").replace("\n", "")
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("ascii")
    outbound = request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": api_key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with request.build_opener(_NoRedirect).open(outbound, timeout=30) as response:
            response_bytes = response.read(MAX_RESPONSE_BYTES + 1)
            if len(response_bytes) > MAX_RESPONSE_BYTES:
                raise PostizConnectorError("Postiz response is too large")
            return json.loads(response_bytes)
    except (error.HTTPError, error.URLError, TimeoutError, OSError, ValueError):
        raise PostizConnectorError("Postiz media upload outcome requires reconciliation") from None


def _approved_media(verified: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve only exact, review-bound Studio media bytes."""
    from pipeline import studio

    bundle = verified["bundle"]
    review = bundle.get("review", {})
    packet_refs = [
        item["ref"] for item in review.get("evidence", [])
        if isinstance(item, dict)
        and isinstance(item.get("ref"), str)
        and item["ref"].startswith("evidence/packets/broker-materials/")
    ]
    if not packet_refs:
        return []
    if len(packet_refs) != 1:
        raise PostizConnectorError("approved release has ambiguous material packets")
    workspace = verified["workspace"]
    profile = verified["profile"]
    packet_ref = packet_refs[0]
    packet_bytes = release_core._state_bytes(workspace, profile, packet_ref)
    assert packet_bytes is not None
    release_core._receipt_covering(
        workspace, profile, bundle["source_run_id"], packet_ref, packet_bytes
    )
    try:
        packet = json.loads(packet_bytes)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise PostizConnectorError("approved material packet is invalid") from None
    expected = {"material_packet_version", "project_id", "project_profile_revision", "channel", "materials"}
    if (not isinstance(packet, dict) or set(packet) != expected
            or packet["material_packet_version"] != "1.0"
            or packet["project_id"] != profile["project_id"]
            or packet["project_profile_revision"] != profile["profile_revision"]
            or not isinstance(packet["materials"], list)
            or len(packet["materials"]) > 10):
        raise PostizConnectorError("approved material packet identity is invalid")
    result = []
    descriptor_fields = {
        "material_id", "record_ref", "record_sha256", "name", "mime_type",
        "size_bytes", "content_sha256", "processing_status",
    }
    for descriptor in packet["materials"]:
        if not isinstance(descriptor, dict) or set(descriptor) != descriptor_fields:
            raise PostizConnectorError("approved material descriptor is invalid")
        asset = studio.material_asset(workspace, profile, descriptor["material_id"])
        if any(asset.get(key) != descriptor[key] for key in descriptor_fields):
            raise PostizConnectorError("approved material differs from its exact review packet")
        if asset["mime_type"] in set(studio.TEXT_TYPES.values()):
            continue
        if asset["mime_type"] not in POSTIZ_MEDIA_TYPES:
            raise PostizConnectorError(
                f"approved material {asset['name']} needs local conversion before Postiz"
            )
        result.append(asset)
    return result


def deliver(
    workspace_path: Path,
    profile_path: Path,
    *,
    release_package: dict[str, str],
    platform: str,
    operation: str,
    requested_at: str,
    scheduled_at: str | None = None,
    transport: Callable[[str, str, bytes], Any] = _post_json,
    media_transport: Callable[[str, str, str, str, bytes], Any] = _post_media,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Deliver approved text once, or stop for reconciliation after ambiguity."""
    if not RFC3339_UTC_RE.fullmatch(requested_at or ""):
        raise PostizConnectorError("requested_at must be RFC3339 UTC seconds")
    if operation == "schedule" and not RFC3339_UTC_RE.fullmatch(scheduled_at or ""):
        raise PostizConnectorError("scheduled_at must be RFC3339 UTC seconds")
    if operation != "schedule" and scheduled_at is not None:
        raise PostizConnectorError("scheduled_at is only valid for schedule")
    if operation == "schedule":
        requested_moment = dt.datetime.fromisoformat(requested_at.replace("Z", "+00:00"))
        scheduled_moment = dt.datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
        if scheduled_moment <= requested_moment:
            raise PostizConnectorError("scheduled_at must be later than requested_at")
    verified = release_core.load_verified_release_package(
        workspace_path, profile_path, release_package
    )
    workspace = verified["workspace"]
    profile = verified["profile"]
    package = verified["package"]
    source_bytes = verified["bundle"]["artifact_bytes"]
    media_type = verified["bundle"]["artifact_media_type"]
    if media_type not in {"text/plain", "text/markdown"} or len(source_bytes) > 100_000:
        raise PostizConnectorError("Postiz v1 accepts one bounded text artifact here")
    try:
        content = source_bytes.decode("utf-8")
    except UnicodeError:
        raise PostizConnectorError("approved artifact must be UTF-8 text") from None
    config = _configuration(workspace, platform, operation)
    media = _approved_media(verified)
    env = os.environ if environment is None else environment
    api_key = env.get("POSTIZ_API_KEY", "")
    if not isinstance(api_key, str) or not api_key.strip() or len(api_key) > 4096:
        raise PostizConnectorError("POSTIZ_API_KEY is unavailable")
    media_sha256 = [item["content_sha256"] for item in media]
    identity = "\n".join([
        package["release_id"], platform, operation, scheduled_at or "",
        config["integration_id"], *media_sha256,
    ])
    intent_id = str(uuid.uuid5(INTENT_NAMESPACE, identity))
    intent_ref = f"records/publisher-intents/{intent_id}.json"
    receipt_ref = f"records/publisher-receipts/{intent_id}.json"
    intent_run_id = f"publisher-intent-{intent_id.replace('-', '')[:20]}"
    receipt_run_id = f"publisher-receipt-{intent_id.replace('-', '')[:20]}"
    intent = {
        "publisher_intent_version": "1.0",
        "intent_id": intent_id,
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "release_package": verified["pointer"],
        "platform": platform,
        "operation": operation,
        "scheduled_at": scheduled_at,
        "integration_sha256": _sha256(config["integration_id"]),
        "content_sha256": package["artifact_sha256"],
        "media_sha256": media_sha256,
        "requested_at": requested_at,
    }
    intent_content = release_core._canonical_json(intent)
    existing_receipt = release_core._state_bytes(workspace, profile, receipt_ref, missing_ok=True)
    if existing_receipt is not None:
        release_core._receipt_covering(
            workspace, profile, receipt_run_id, receipt_ref, existing_receipt
        )
        try:
            receipt = json.loads(existing_receipt)
        except (UnicodeError, ValueError, TypeError):
            raise PostizConnectorError("stored Postiz receipt is invalid") from None
        if (receipt.get("intent_id") != intent_id
                or receipt.get("content_sha256") != package["artifact_sha256"]
                or receipt.get("release_id") != package["release_id"]
                or receipt.get("platform") != platform
                or receipt.get("operation") != operation
                or receipt.get("integration_sha256") != _sha256(config["integration_id"])
                or receipt.get("media_sha256") != media_sha256
                or receipt.get("status") not in {"drafted", "scheduled", "submitted"}
                or receipt.get("publicly_live") is not False):
            raise PostizConnectorError("stored Postiz receipt does not match the approved release")
        return {"status": receipt["status"], "reused": True, "intent_id": intent_id,
                "external_side_effects": True, "publicly_live": False,
                "receipt": {"artifact_ref": receipt_ref,
                            "artifact_revision": intent_id,
                            "artifact_sha256": hashlib.sha256(existing_receipt).hexdigest(),
                            "byte_size": len(existing_receipt)}}
    existing_intent = release_core._state_bytes(workspace, profile, intent_ref, missing_ok=True)
    if existing_intent is not None:
        if existing_intent != intent_content:
            raise PostizConnectorError("stored Postiz intent conflicts with this request")
        release_core._receipt_covering(
            workspace, profile, intent_run_id, intent_ref, existing_intent
        )
        raise PostizConnectorError("Postiz request outcome requires reconciliation")
    intent_request = release_core._writer_request(
        profile,
        intent_run_id,
        intent_id,
        [release_core._write_item(intent_ref, intent_content)],
    )
    release_core._apply_request(workspace, verified["profile_path"], intent_request)
    uploaded_media = []
    for asset in media:
        uploaded = media_transport(
            f"{config['base_url']}/upload", api_key, asset["name"],
            asset["mime_type"], asset["content"],
        )
        if (not isinstance(uploaded, dict)
                or not isinstance(uploaded.get("id"), str) or not uploaded["id"].strip()
                or not isinstance(uploaded.get("path"), str)):
            raise PostizConnectorError("Postiz media response requires reconciliation")
        parsed_media = urlsplit(uploaded["path"])
        if (parsed_media.scheme != "https" or not parsed_media.hostname
                or parsed_media.username or parsed_media.password
                or len(uploaded["path"]) > 2048):
            raise PostizConnectorError("Postiz media response requires reconciliation")
        uploaded_media.append({"id": uploaded["id"], "path": uploaded["path"]})
    payload = release_core._canonical_json({
        "type": operation,
        "date": scheduled_at or requested_at,
        "shortLink": False,
        "tags": [],
        "posts": [{
            "integration": {"id": config["integration_id"]},
            "value": [{"content": content, "image": uploaded_media}],
            "settings": config["settings"],
        }],
    })
    response = transport(f"{config['base_url']}/posts", api_key, payload)
    if (not isinstance(response, list) or len(response) != 1
            or not isinstance(response[0], dict)
            or not isinstance(response[0].get("postId"), str)
            or not response[0]["postId"].strip()
            or response[0].get("integration") != config["integration_id"]):
        raise PostizConnectorError("Postiz response requires reconciliation")
    receipt = {
        "publisher_receipt_version": "1.0",
        "intent_id": intent_id,
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "release_id": package["release_id"],
        "platform": platform,
        "operation": operation,
        "status": {"draft": "drafted", "schedule": "scheduled", "now": "submitted"}[operation],
        "post_id": response[0]["postId"],
        "integration_sha256": _sha256(config["integration_id"]),
        "content_sha256": package["artifact_sha256"],
        "media_sha256": media_sha256,
        "publicly_live": False,
        "completed_at": requested_at,
    }
    receipt_content = release_core._canonical_json(receipt)
    receipt_request = release_core._writer_request(
        profile,
        receipt_run_id,
        str(uuid.uuid5(INTENT_NAMESPACE, f"receipt\n{intent_id}")),
        [release_core._write_item(receipt_ref, receipt_content)],
    )
    release_core._apply_request(workspace, verified["profile_path"], receipt_request)
    return {"status": receipt["status"], "reused": False, "intent_id": intent_id,
            "external_side_effects": True, "publicly_live": False,
            "receipt": {"artifact_ref": receipt_ref,
                        "artifact_revision": intent_id,
                        "artifact_sha256": hashlib.sha256(receipt_content).hexdigest(),
                        "byte_size": len(receipt_content)}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--project-config", required=True, type=Path)
    parser.add_argument("--release-package-ref", required=True)
    parser.add_argument("--release-package-revision", required=True)
    parser.add_argument("--release-package-sha256", required=True)
    parser.add_argument("--platform", required=True, choices=sorted(PLATFORMS))
    parser.add_argument("--operation", required=True, choices=sorted(OPERATIONS))
    parser.add_argument("--requested-at", required=True)
    parser.add_argument("--scheduled-at")
    args = parser.parse_args(argv)
    try:
        result = deliver(
            args.workspace,
            args.project_config,
            release_package={
                "artifact_ref": args.release_package_ref,
                "artifact_revision": args.release_package_revision,
                "artifact_sha256": args.release_package_sha256,
            },
            platform=args.platform,
            operation=args.operation,
            requested_at=args.requested_at,
            scheduled_at=args.scheduled_at,
        )
    except (PostizConnectorError, release_core.ReleaseError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
