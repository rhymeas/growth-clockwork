#!/usr/bin/env python3
"""Refresh profile-local source, section, and manifest SHA-256 pins.

This is a development helper for authoring immutable context revisions. It never
touches runtime state. Use it before a context revision is frozen; after release,
create a new revision path instead of refreshing an existing one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


class RefreshError(ValueError):
    pass


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RefreshError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RefreshError(f"{label} must be a JSON object: {path}")
    return value


def _write_object(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve(profile_root: Path, raw: Any, label: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise RefreshError(f"{label} must be a non-empty relative path")
    relative = Path(raw)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise RefreshError(f"{label} escapes the profile")
    current = profile_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RefreshError(f"{label} traverses a symlink")
    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(profile_root)
    except (OSError, ValueError) as exc:
        raise RefreshError(f"{label} is missing or escapes the profile") from exc
    if not resolved.is_file():
        raise RefreshError(f"{label} is not a regular file")
    return resolved


def refresh(profile_root: Path, manifest_ref: str) -> dict[str, str]:
    profile_root = profile_root.resolve(strict=True)
    manifest_path = _resolve(profile_root, manifest_ref, "manifest")
    manifest = _read_object(manifest_path, "manifest")
    sections = manifest.get("sections")
    if not isinstance(sections, list) or len(sections) != 8:
        raise RefreshError("manifest must contain exactly eight sections")

    for index, entry in enumerate(sections):
        if not isinstance(entry, dict):
            raise RefreshError(f"manifest section {index} must be an object")
        section_path = _resolve(
            profile_root, entry.get("artifact_ref"), f"manifest section {index}"
        )
        section = _read_object(section_path, f"section {index}")
        sources = section.get("source_refs")
        if not isinstance(sources, list) or not sources:
            raise RefreshError(f"section {index} must contain source_refs")
        for source_index, source in enumerate(sources):
            if not isinstance(source, dict):
                raise RefreshError(
                    f"section {index} source {source_index} must be an object"
                )
            source_path = _resolve(
                profile_root,
                source.get("artifact_ref"),
                f"section {index} source {source_index}",
            )
            source["artifact_sha256"] = _sha256(source_path)
        _write_object(section_path, section)
        entry["artifact_sha256"] = _sha256(section_path)

    _write_object(manifest_path, manifest)
    return {
        "artifact_ref": manifest_ref,
        "artifact_revision": str(manifest["context_revision"]),
        "artifact_sha256": _sha256(manifest_path),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-root", required=True, type=Path)
    parser.add_argument("manifest_ref")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = refresh(args.profile_root, args.manifest_ref)
    except (RefreshError, OSError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

