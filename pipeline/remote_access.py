#!/usr/bin/env python3
"""Validate the private, opt-in Tailscale Serve boundary for Project Desk."""
from __future__ import annotations

import json
from pathlib import Path
import stat
from typing import Any
from urllib.parse import urlsplit


CONFIG_VERSION = 1
CONFIG_PATH = Path("runtime/remote-access.json")
MAX_CONFIG_BYTES = 16 * 1024


class RemoteAccessError(ValueError):
    pass


def _disabled() -> dict[str, Any]:
    return {
        "version": CONFIG_VERSION,
        "enabled": False,
        "provider": "tailscale-serve",
        "origin": None,
        "allowed_logins": [],
    }


def load(workspace: Path) -> dict[str, Any]:
    """Load one credential-free allowlist. Missing means local-only."""

    path = Path(workspace).resolve() / CONFIG_PATH
    if not path.exists():
        return _disabled()
    try:
        if path.is_symlink():
            raise OSError()
        info = path.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONFIG_BYTES
                or info.st_mode & 0o077):
            raise OSError()
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RemoteAccessError("Remote access configuration is unavailable or invalid") from exc
    expected = {"version", "enabled", "provider", "origin", "allowed_logins"}
    if not isinstance(value, dict) or set(value) != expected:
        raise RemoteAccessError("Remote access configuration has an invalid shape")
    if value["version"] != CONFIG_VERSION or value["provider"] != "tailscale-serve":
        raise RemoteAccessError("Remote access configuration uses an unsupported version or provider")
    if not isinstance(value["enabled"], bool):
        raise RemoteAccessError("Remote access enabled must be true or false")
    if not value["enabled"]:
        if value["origin"] is not None or value["allowed_logins"] != []:
            raise RemoteAccessError("Disabled remote access must not retain an origin or login")
        return _disabled()
    origin = value["origin"]
    logins = value["allowed_logins"]
    if not isinstance(origin, str) or len(origin) > 512:
        raise RemoteAccessError("Remote access origin is invalid")
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError as exc:
        raise RemoteAccessError("Remote access origin is invalid") from exc
    hostname = parsed.hostname
    if (
        parsed.scheme != "https"
        or hostname is None
        or not hostname.endswith(".ts.net")
        or hostname == ".ts.net"
        or port is not None
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or origin != f"https://{hostname}"
    ):
        raise RemoteAccessError("Remote access requires one canonical HTTPS .ts.net origin")
    if (
        not isinstance(logins, list)
        or not 1 <= len(logins) <= 8
        or any(not isinstance(item, str) or not item or len(item) > 320 or item != item.strip() for item in logins)
        or len({item.casefold() for item in logins}) != len(logins)
    ):
        raise RemoteAccessError("Remote access requires one to eight unique Tailscale logins")
    return {
        "version": CONFIG_VERSION,
        "enabled": True,
        "provider": "tailscale-serve",
        "origin": origin,
        "allowed_logins": list(logins),
    }


def readiness(workspace: Path) -> dict[str, Any]:
    config = load(workspace)
    return {
        "state": "configured" if config["enabled"] else "local_only",
        "provider": config["provider"],
    }
