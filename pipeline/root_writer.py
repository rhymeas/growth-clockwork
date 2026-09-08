#!/usr/bin/env python3
"""Deterministic, project-profile-driven writer for Growth Pipeline artifacts.

The writer accepts immutable structured UTF-8 create requests only. Revisions use
new paths; in-place replacement and deletion are not supported. It never reasons,
publishes, invokes a shell, or reaches the network. Project-specific paths and
limits live in a JSON profile outside this module.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
from typing import Any, Iterator
import uuid


REQUEST_VERSION = "1.0"
RECEIPT_VERSION = "1.0"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STAGE_NAME_RE = re.compile(r"^[0-9a-f]{32}\.tmp$")


class WriterError(ValueError):
    """A deterministic request or boundary failure."""


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError) as exc:
        raise WriterError("Request must be canonical UTF-8 JSON") from exc


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WriterError(f"JSON file does not exist: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WriterError(f"Cannot read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WriterError(f"Expected a JSON object: {path}")
    return value


def _require_exact_keys(
    value: dict[str, Any], required: set[str], optional: set[str], label: str
) -> None:
    missing = sorted(required - value.keys())
    extra = sorted(value.keys() - required - optional)
    if missing:
        raise WriterError(f"{label} missing fields: {', '.join(missing)}")
    if extra:
        raise WriterError(f"{label} has unknown fields: {', '.join(extra)}")


def _validate_relative_path(raw: Any, label: str) -> PurePosixPath:
    if not isinstance(raw, str) or not raw:
        raise WriterError(f"{label} must be a non-empty string")
    if "\\" in raw or "\x00" in raw or any(ord(char) < 32 for char in raw):
        raise WriterError(f"{label} contains forbidden characters")
    path = PurePosixPath(raw)
    if not path.parts or raw == "." or path.is_absolute() or raw.endswith("/"):
        raise WriterError(f"{label} must be a relative file path")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise WriterError(f"{label} contains an unsafe path segment")
    if path.parts[0] == ".git" or ".git" in path.parts:
        raise WriterError(f"{label} may not enter .git")
    return path


def _under_prefix(path: PurePosixPath, prefix: PurePosixPath) -> bool:
    return path == prefix or path.parts[: len(prefix.parts)] == prefix.parts


def _validate_prefixes(raw: Any, label: str) -> tuple[PurePosixPath, ...]:
    if not isinstance(raw, list) or not raw:
        raise WriterError(f"{label} must be a non-empty array")
    prefixes = tuple(_validate_relative_path(item, label) for item in raw)
    if len(set(prefixes)) != len(prefixes):
        raise WriterError(f"{label} contains duplicates")
    return prefixes


def _validate_portable_ids(raw: Any, label: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise WriterError(f"{label} must be a non-empty array")
    if not all(isinstance(item, str) and RUN_ID_RE.fullmatch(item) for item in raw):
        raise WriterError(f"{label} contains an invalid portable identifier")
    if len(set(raw)) != len(raw):
        raise WriterError(f"{label} contains duplicates")
    return tuple(raw)


def _validate_pinned_reference(raw: Any, label: str) -> dict[str, str]:
    if not isinstance(raw, dict) or set(raw) != {
        "artifact_ref",
        "artifact_revision",
        "artifact_sha256",
    }:
        raise WriterError(
            f"{label} must contain artifact_ref, artifact_revision, and artifact_sha256"
        )
    relative = _validate_relative_path(raw["artifact_ref"], f"{label}.artifact_ref")
    revision = raw["artifact_revision"]
    digest = raw["artifact_sha256"]
    if not isinstance(revision, str) or not RUN_ID_RE.fullmatch(revision):
        raise WriterError(f"{label}.artifact_revision is invalid")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise WriterError(f"{label}.artifact_sha256 is invalid")
    return {
        "artifact_ref": relative.as_posix(),
        "artifact_revision": revision,
        "artifact_sha256": digest,
    }


def load_project_profile(path: Path) -> dict[str, Any]:
    profile = _read_json(path)
    _require_exact_keys(
        profile,
        {
            "profile_version",
            "profile_revision",
            "project_id",
            "root_role_id",
            "writer",
        },
        {
            "agent_registry",
            "display_name",
            "enabled_routes",
            "mandatory_roles",
            "product_motion",
            "professional_contracts",
            "project_context",
            "research_adapters",
            "schema_registry",
            "role_ids",
            "task_id_pattern",
        },
        "project profile",
    )
    if profile["profile_version"] != "1.0":
        raise WriterError("Unsupported project profile version")
    if not isinstance(profile["profile_revision"], str) or not RUN_ID_RE.fullmatch(
        profile["profile_revision"]
    ):
        raise WriterError("profile_revision has an invalid portable identifier")
    if not isinstance(profile["project_id"], str) or not RUN_ID_RE.fullmatch(
        profile["project_id"]
    ):
        raise WriterError("project_id has an invalid portable identifier")
    if not isinstance(profile["root_role_id"], str) or not RUN_ID_RE.fullmatch(
        profile["root_role_id"]
    ):
        raise WriterError("root_role_id has an invalid portable identifier")

    if "role_ids" in profile:
        profile["_role_ids"] = _validate_portable_ids(
            profile["role_ids"], "role_ids"
        )
    if "enabled_routes" in profile:
        profile["_enabled_routes"] = _validate_portable_ids(
            profile["enabled_routes"], "enabled_routes"
        )
    if "mandatory_roles" in profile:
        profile["_mandatory_roles"] = _validate_portable_ids(
            profile["mandatory_roles"], "mandatory_roles"
        )
        if "role_ids" not in profile or not set(profile["mandatory_roles"]).issubset(
            profile["role_ids"]
        ):
            raise WriterError("mandatory_roles must be a subset of role_ids")
    if "task_id_pattern" in profile:
        pattern = profile["task_id_pattern"]
        if not isinstance(pattern, str) or not pattern.startswith("^") or not pattern.endswith("$"):
            raise WriterError("task_id_pattern must be an anchored regular expression")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise WriterError(f"task_id_pattern is invalid: {exc}") from exc
    if "product_motion" in profile and (
        not isinstance(profile["product_motion"], str)
        or not RUN_ID_RE.fullmatch(profile["product_motion"])
    ):
        raise WriterError("product_motion has an invalid portable identifier")
    if "project_context" in profile:
        profile["_project_context"] = _validate_pinned_reference(
            profile["project_context"], "project_context"
        )
        if not profile["_project_context"]["artifact_ref"].startswith(
            "context/manifests/"
        ):
            raise WriterError("project_context must point below context/manifests")
    if "agent_registry" in profile:
        profile["_agent_registry"] = _validate_pinned_reference(
            profile["agent_registry"], "agent_registry"
        )
        if not profile["_agent_registry"]["artifact_ref"].startswith("agents/"):
            raise WriterError("agent_registry must point below agents")
    if "professional_contracts" in profile:
        binding = profile["professional_contracts"]
        if not isinstance(binding, dict):
            raise WriterError("professional_contracts must be an object")
        _require_exact_keys(
            binding,
            {"schema", "requirements"},
            set(),
            "professional_contracts",
        )
        normalized_contracts = {
            name: _validate_pinned_reference(
                binding[name], f"professional_contracts.{name}"
            )
            for name in ("schema", "requirements")
        }
        for name, pointer in normalized_contracts.items():
            if not pointer["artifact_ref"].startswith("contracts/"):
                raise WriterError(
                    f"professional_contracts.{name} must point below contracts"
                )
        profile["_professional_contracts"] = normalized_contracts
    if "research_adapters" in profile:
        pointer = _validate_pinned_reference(
            profile["research_adapters"], "research_adapters"
        )
        if not pointer["artifact_ref"].startswith("contracts/"):
            raise WriterError("research_adapters must point below contracts")
        profile["_research_adapters"] = pointer

    writer = profile["writer"]
    if not isinstance(writer, dict):
        raise WriterError("writer must be an object")
    _require_exact_keys(
        writer,
        {
            "state_root",
            "allowed_roots",
            "append_only_roots",
            "receipt_root",
            "max_files_per_request",
            "max_total_bytes",
        },
        set(),
        "writer policy",
    )
    state_root = _validate_relative_path(writer["state_root"], "state_root")
    allowed = _validate_prefixes(writer["allowed_roots"], "allowed_roots")
    append_only = _validate_prefixes(
        writer["append_only_roots"], "append_only_roots"
    )
    receipt_root = _validate_relative_path(writer["receipt_root"], "receipt_root")
    for prefix in append_only:
        if not any(_under_prefix(prefix, allowed_prefix) for allowed_prefix in allowed):
            raise WriterError(f"append-only root is not allowed: {prefix}")
    if not any(_under_prefix(receipt_root, prefix) for prefix in allowed):
        raise WriterError("receipt_root is not allowed")
    if not any(_under_prefix(receipt_root, prefix) for prefix in append_only):
        raise WriterError("receipt_root must be append-only")
    for field in ("max_files_per_request", "max_total_bytes"):
        if not isinstance(writer[field], int) or isinstance(writer[field], bool):
            raise WriterError(f"{field} must be an integer")
        if writer[field] < 1:
            raise WriterError(f"{field} must be positive")

    profile["_allowed_roots"] = allowed
    profile["_append_only_roots"] = append_only
    profile["_receipt_root"] = receipt_root
    profile["_state_root"] = state_root
    return profile


def _validate_workspace(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise WriterError(f"Workspace cannot be resolved: {path}") from exc
    if not resolved.is_dir():
        raise WriterError("Workspace must be a directory")
    if resolved in {Path("/"), Path.home().resolve()}:
        raise WriterError("Refusing a broad workspace root")
    if not ((resolved / "AGENTS.md").is_file() or (resolved / ".git").is_dir()):
        raise WriterError("Workspace must contain AGENTS.md or .git")
    return resolved


def _assert_no_symlink_path(workspace: Path, relative: PurePosixPath) -> Path:
    current = workspace
    for index, part in enumerate(relative.parts):
        current = current / part
        if current.is_symlink():
            raise WriterError(f"Symlink path is forbidden: {relative}")
        if (
            index < len(relative.parts) - 1
            and current.exists()
            and not current.is_dir()
        ):
            raise WriterError(f"Non-directory path ancestor is forbidden: {relative}")
    try:
        current.relative_to(workspace)
    except ValueError as exc:
        raise WriterError(f"Path escapes workspace: {relative}") from exc
    return current


def _validate_profile_package(
    workspace: Path, profile_path: Path, profile: dict[str, Any]
) -> Path:
    try:
        resolved = profile_path.resolve(strict=True)
    except OSError as exc:
        raise WriterError(f"Project profile cannot be resolved: {profile_path}") from exc
    if not resolved.is_file() or resolved.name != "project.json":
        raise WriterError("Project profile must be a project.json file")

    projects_root = workspace / "projects"
    try:
        package_relative = resolved.relative_to(projects_root)
    except ValueError as exc:
        raise WriterError("Project profile must live below workspace/projects") from exc
    if len(package_relative.parts) != 2:
        raise WriterError("Project profile must live at projects/<profile>/project.json")

    expected_state = PurePosixPath("projects") / package_relative.parts[0] / "state"
    if profile["_state_root"] != expected_state:
        raise WriterError(
            "writer.state_root must equal the selected profile package state directory"
        )
    return _assert_no_symlink_path(workspace, expected_state)


def _validate_request_header(
    request: dict[str, Any], profile: dict[str, Any]
) -> None:
    _require_exact_keys(
        request,
        {
            "writer_request_version",
            "project_id",
            "project_profile_revision",
            "run_id",
            "idempotency_key",
            "requested_by",
            "writes",
        },
        {"preconditions"},
        "writer request",
    )
    if request["writer_request_version"] != REQUEST_VERSION:
        raise WriterError("Unsupported writer request version")
    if request["project_id"] != profile["project_id"]:
        raise WriterError("Request project_id does not match the selected profile")
    if request["project_profile_revision"] != profile["profile_revision"]:
        raise WriterError("Request project_profile_revision is stale or mismatched")
    if not isinstance(request["run_id"], str) or not RUN_ID_RE.fullmatch(
        request["run_id"]
    ):
        raise WriterError("run_id has an invalid portable identifier")
    try:
        parsed_key = uuid.UUID(str(request["idempotency_key"]))
    except (ValueError, AttributeError) as exc:
        raise WriterError("idempotency_key must be a UUID") from exc
    if str(parsed_key) != request["idempotency_key"]:
        raise WriterError("idempotency_key must use canonical lowercase UUID form")
    if request["requested_by"] != profile["root_role_id"]:
        raise WriterError("requested_by does not match the project root role")
    if not isinstance(request["writes"], list) or not request["writes"]:
        raise WriterError("writes must be a non-empty array")
    if "preconditions" in request and not isinstance(
        request["preconditions"], list
    ):
        raise WriterError("preconditions must be an array")


def _validate_preconditions(
    request: dict[str, Any],
    profile: dict[str, Any],
    workspace: Path,
) -> list[dict[str, Any]]:
    raw_preconditions = request.get("preconditions", [])
    prepared: list[dict[str, Any]] = []
    seen: set[PurePosixPath] = set()
    for index, item in enumerate(raw_preconditions):
        label = f"preconditions[{index}]"
        if not isinstance(item, dict):
            raise WriterError(f"{label} must be an object")
        _require_exact_keys(item, {"path", "expected_sha256"}, set(), label)
        relative = _validate_relative_path(item["path"], f"{label}.path")
        if relative in seen:
            raise WriterError(f"Duplicate precondition path: {relative}")
        seen.add(relative)
        if not any(
            _under_prefix(relative, prefix) for prefix in profile["_allowed_roots"]
        ):
            raise WriterError(
                f"Precondition path is outside the project allowlist: {relative}"
            )
        if relative in profile["_allowed_roots"]:
            raise WriterError(
                f"Writer roots are directories, not precondition targets: {relative}"
            )
        if _under_prefix(relative, profile["_receipt_root"]):
            raise WriterError(
                f"Writer receipt root is reserved from preconditions: {relative}"
            )
        expected = item["expected_sha256"]
        if expected is not None and (
            not isinstance(expected, str) or not SHA256_RE.fullmatch(expected)
        ):
            raise WriterError(f"{label}.expected_sha256 is invalid")
        _assert_no_symlink_path(workspace, profile["_state_root"] / relative)
        prepared.append(
            {"relative": relative, "expected_sha256": expected}
        )
    return prepared


def _check_preconditions(
    state_fd: int, preconditions: list[dict[str, Any]]
) -> None:
    for item in preconditions:
        relative = item["relative"]
        expected = item["expected_sha256"]
        current = _read_relative_bytes(state_fd, relative, missing_ok=True)
        if expected is None:
            if current is not None:
                raise WriterError(
                    f"Precondition failed; path must be absent: {relative}"
                )
            continue
        if current is None:
            raise WriterError(
                f"Precondition failed; path does not exist: {relative}"
            )
        if _sha256(current) != expected:
            raise WriterError(
                f"Precondition failed; exact byte hash differs: {relative}"
            )


def validate_request(
    request: dict[str, Any],
    profile: dict[str, Any],
    workspace: Path,
    *,
    allow_existing_exact: bool = False,
) -> list[dict[str, Any]]:
    _validate_request_header(request, profile)
    _validate_preconditions(request, profile, workspace)

    policy = profile["writer"]
    if len(request["writes"]) > policy["max_files_per_request"]:
        raise WriterError("Request exceeds max_files_per_request")

    prepared: list[dict[str, Any]] = []
    seen: set[PurePosixPath] = set()
    total_bytes = 0
    for index, item in enumerate(request["writes"]):
        label = f"writes[{index}]"
        if not isinstance(item, dict):
            raise WriterError(f"{label} must be an object")
        _require_exact_keys(
            item,
            {"path", "mode", "content", "content_sha256", "expected_sha256"},
            {"media_type"},
            label,
        )
        relative = _validate_relative_path(item["path"], f"{label}.path")
        if relative in seen:
            raise WriterError(f"Duplicate write path: {relative}")
        seen.add(relative)
        if not any(
            _under_prefix(relative, prefix) for prefix in profile["_allowed_roots"]
        ):
            raise WriterError(f"Path is outside the project allowlist: {relative}")
        if relative in profile["_allowed_roots"]:
            raise WriterError(f"Writer roots are directories, not file targets: {relative}")
        if _under_prefix(relative, profile["_receipt_root"]):
            raise WriterError(f"Writer receipt root is reserved: {relative}")
        if item["mode"] != "create":
            raise WriterError(
                f"{label}.mode must be create; revisions require a new path"
            )
        if not isinstance(item["content"], str):
            raise WriterError(f"{label}.content must be a UTF-8 string")
        try:
            content = item["content"].encode("utf-8")
        except UnicodeEncodeError as exc:
            raise WriterError(f"{label}.content must be valid UTF-8") from exc
        total_bytes += len(content)
        if not isinstance(item["content_sha256"], str) or not SHA256_RE.fullmatch(
            item["content_sha256"]
        ):
            raise WriterError(f"{label}.content_sha256 is invalid")
        if _sha256(content) != item["content_sha256"]:
            raise WriterError(f"Content hash mismatch for {relative}")
        expected = item["expected_sha256"]
        if expected is not None:
            raise WriterError(f"Create requires expected_sha256=null: {relative}")
        media_type = item.get("media_type", "text/plain; charset=utf-8")
        if not isinstance(media_type, str) or not media_type.strip():
            raise WriterError(f"{label}.media_type must be a non-empty string")

        target = _assert_no_symlink_path(
            workspace, profile["_state_root"] / relative
        )
        if target.exists() and not allow_existing_exact:
            raise WriterError(f"Create target already exists: {relative}")

        prepared.append(
            {
                "relative": relative,
                "mode": "create",
                "content": content,
                "content_sha256": item["content_sha256"],
                "media_type": media_type,
            }
        )

    if total_bytes > policy["max_total_bytes"]:
        raise WriterError("Request exceeds max_total_bytes")
    return prepared


def _receipt_relative(
    profile: dict[str, Any], request: dict[str, Any]
) -> PurePosixPath:
    return profile["_receipt_root"] / request["run_id"] / (
        f"writer-{request['idempotency_key']}.json"
    )


def _directory_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise WriterError("Root Writer requires POSIX O_DIRECTORY and O_NOFOLLOW")
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _open_directory_chain(
    start_fd: int,
    parts: tuple[str, ...],
    *,
    create: bool,
    label: str,
) -> int:
    current_fd = os.dup(start_fd)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise WriterError(f"Cannot create {label}: {exc}") from exc
            try:
                next_fd = os.open(part, _directory_flags(), dir_fd=current_fd)
            except FileNotFoundError:
                if not create:
                    raise
                raise WriterError(f"Directory disappeared while opening {label}")
            except OSError as exc:
                raise WriterError(
                    f"Unsafe or unreadable directory while opening {label}: {exc}"
                ) from exc
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


@contextlib.contextmanager
def _project_state_fd(workspace: Path, state_root: PurePosixPath) -> Iterator[int]:
    try:
        workspace_fd = os.open(workspace, _directory_flags())
    except OSError as exc:
        raise WriterError(f"Cannot open workspace directory safely: {exc}") from exc
    try:
        state_fd = _open_directory_chain(
            workspace_fd,
            state_root.parts,
            create=True,
            label="project state root",
        )
    finally:
        os.close(workspace_fd)
    try:
        yield state_fd
    finally:
        os.close(state_fd)


def _read_relative_bytes(
    state_fd: int,
    relative: PurePosixPath,
    *,
    missing_ok: bool = False,
) -> bytes | None:
    try:
        parent_fd = _open_directory_chain(
            state_fd,
            relative.parts[:-1],
            create=False,
            label=f"parent of {relative}",
        )
    except FileNotFoundError:
        if missing_ok:
            return None
        raise WriterError(f"File does not exist: {relative}")
    try:
        try:
            file_fd = os.open(
                relative.name,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            if missing_ok:
                return None
            raise WriterError(f"File does not exist: {relative}")
        except OSError as exc:
            raise WriterError(f"Cannot open regular file safely: {relative}: {exc}") from exc
        try:
            opened = os.fstat(file_fd)
            if not stat.S_ISREG(opened.st_mode):
                raise WriterError(f"Path is not a regular file: {relative}")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(file_fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            current = os.stat(
                relative.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                raise WriterError(f"File changed while reading: {relative}")
            return b"".join(chunks)
        finally:
            os.close(file_fd)
    finally:
        os.close(parent_fd)


def _write_all(file_fd: int, content: bytes) -> None:
    remaining = memoryview(content)
    while remaining:
        written = os.write(file_fd, remaining)
        if written < 1:
            raise WriterError("Short write while staging content")
        remaining = remaining[written:]


def _cleanup_transaction_stages(transaction_fd: int) -> None:
    changed = False
    try:
        names = os.listdir(transaction_fd)
    except OSError as exc:
        raise WriterError(f"Cannot inspect writer transaction directory: {exc}") from exc
    for name in names:
        if not STAGE_NAME_RE.fullmatch(name):
            continue
        try:
            item = os.stat(name, dir_fd=transaction_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(item.st_mode):
            continue
        try:
            os.unlink(name, dir_fd=transaction_fd)
            changed = True
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise WriterError(f"Cannot remove stale writer stage {name}: {exc}") from exc
    if changed:
        os.fsync(transaction_fd)


def _publish_exclusive(
    state_fd: int,
    transaction_fd: int,
    relative: PurePosixPath,
    content: bytes,
) -> None:
    stage_name = f"{uuid.uuid4().hex}.tmp"
    try:
        stage_fd = os.open(
            stage_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=transaction_fd,
        )
    except OSError as exc:
        raise WriterError(f"Cannot create transaction stage: {exc}") from exc
    linked = False
    try:
        try:
            _write_all(stage_fd, content)
            os.fsync(stage_fd)
            staged = os.fstat(stage_fd)
        finally:
            os.close(stage_fd)

        parent_fd = _open_directory_chain(
            state_fd,
            relative.parts[:-1],
            create=True,
            label=f"parent of {relative}",
        )
        try:
            try:
                os.link(
                    stage_name,
                    relative.name,
                    src_dir_fd=transaction_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise WriterError(f"Create target already exists: {relative}") from exc
            except OSError as exc:
                raise WriterError(f"Cannot publish immutable file {relative}: {exc}") from exc
            linked = True
            published = os.stat(
                relative.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (staged.st_dev, staged.st_ino) != (
                published.st_dev,
                published.st_ino,
            ):
                raise WriterError(f"Published file identity mismatch: {relative}")
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        try:
            os.unlink(stage_name, dir_fd=transaction_fd)
            os.fsync(transaction_fd)
        except FileNotFoundError:
            if not linked:
                raise WriterError("Transaction stage disappeared before publish")


def _json_object_from_bytes(content: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WriterError(f"Cannot parse {label} JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise WriterError(f"{label} must be a JSON object")
    return value


def _idempotent_receipt(
    receipt_relative: PurePosixPath,
    request_sha256: str,
    state_fd: int,
    request: dict[str, Any],
) -> dict[str, Any] | None:
    receipt_bytes = _read_relative_bytes(
        state_fd,
        receipt_relative,
        missing_ok=True,
    )
    if receipt_bytes is None:
        return None
    receipt = _json_object_from_bytes(receipt_bytes, "writer receipt")
    _require_exact_keys(
        receipt,
        {
            "receipt_version",
            "status",
            "project_id",
            "project_profile_revision",
            "run_id",
            "idempotency_key",
            "requested_by",
            "request_sha256",
            "writes",
            "created_at",
        },
        set(),
        "writer receipt",
    )
    if receipt.get("receipt_version") != RECEIPT_VERSION:
        raise WriterError("Existing receipt version is unsupported")
    if receipt.get("request_sha256") != request_sha256:
        raise WriterError("Idempotency key was already used for another request")
    if receipt.get("status") != "completed":
        raise WriterError("Existing receipt is not completed")
    for field in (
        "project_id",
        "project_profile_revision",
        "run_id",
        "idempotency_key",
        "requested_by",
    ):
        if receipt.get(field) != request[field]:
            raise WriterError(f"Existing receipt identity mismatch: {field}")
    writes = receipt.get("writes")
    if not isinstance(writes, list) or not writes:
        raise WriterError("Existing receipt has invalid writes")
    for item in writes:
        if not isinstance(item, dict):
            raise WriterError("Existing receipt has an invalid write entry")
        _require_exact_keys(
            item,
            {"path", "mode", "sha256", "bytes", "media_type"},
            set(),
            "receipt write",
        )
        if item["mode"] != "create":
            raise WriterError("Existing receipt has an invalid write mode")
        if not isinstance(item["sha256"], str) or not SHA256_RE.fullmatch(
            item["sha256"]
        ):
            raise WriterError("Existing receipt has an invalid write hash")
        if (
            not isinstance(item["bytes"], int)
            or isinstance(item["bytes"], bool)
            or item["bytes"] < 0
        ):
            raise WriterError("Existing receipt has an invalid byte count")
        if not isinstance(item["media_type"], str) or not item["media_type"]:
            raise WriterError("Existing receipt has an invalid media type")
        relative = _validate_relative_path(item.get("path"), "receipt write path")
        target = _read_relative_bytes(state_fd, relative)
        assert target is not None
        if (
            _sha256(target) != item["sha256"]
            or len(target) != item["bytes"]
        ):
            raise WriterError(f"Idempotent readback mismatch: {relative}")
    return receipt


@contextlib.contextmanager
def _workspace_lock(workspace: Path) -> Iterator[None]:
    fingerprint = _sha256(str(workspace).encode("utf-8"))[:24]
    lock_path = Path(tempfile.gettempdir()) / f"growth-pipeline-writer-{fingerprint}.lock"
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def apply_request(
    workspace_path: Path, profile_path: Path, request_path: Path
) -> dict[str, Any]:
    return apply_request_value(workspace_path, profile_path, _read_json(request_path))


def apply_request_value(
    workspace_path: Path, profile_path: Path, request: dict[str, Any]
) -> dict[str, Any]:
    """Apply an in-memory request without spilling its content to a temp file."""
    workspace = _validate_workspace(workspace_path)
    profile = load_project_profile(profile_path)
    _validate_profile_package(workspace, profile_path, profile)
    if not isinstance(request, dict):
        raise WriterError("Writer request must be a JSON object")
    _validate_request_header(request, profile)
    request_sha256 = _sha256(_canonical_json(request))
    receipt_relative = _receipt_relative(profile, request)
    if not any(
        _under_prefix(receipt_relative, prefix)
        for prefix in profile["_allowed_roots"]
    ):
        raise WriterError("Project profile does not allow writer receipts")
    if not any(
        _under_prefix(receipt_relative, prefix)
        for prefix in profile["_append_only_roots"]
    ):
        raise WriterError("Writer receipt path must be append-only")
    with _workspace_lock(workspace):
        with _project_state_fd(workspace, profile["_state_root"]) as state_fd:
            transaction_fd = _open_directory_chain(
                state_fd,
                (".writer-tx",),
                create=True,
                label="writer transaction directory",
            )
            try:
                os.fchmod(transaction_fd, 0o700)
                _cleanup_transaction_stages(transaction_fd)
                existing = _idempotent_receipt(
                    receipt_relative,
                    request_sha256,
                    state_fd,
                    request,
                )
                if existing is not None:
                    return existing

                prepared = validate_request(
                    request,
                    profile,
                    workspace,
                    allow_existing_exact=True,
                )
                preconditions = _validate_preconditions(
                    request,
                    profile,
                    workspace,
                )
                _check_preconditions(state_fd, preconditions)
                applied: list[dict[str, Any]] = []
                try:
                    for item in prepared:
                        existing_bytes = _read_relative_bytes(
                            state_fd,
                            item["relative"],
                            missing_ok=True,
                        )
                        if existing_bytes is not None:
                            if existing_bytes != item["content"]:
                                raise WriterError(
                                    "Existing immutable target differs from the "
                                    f"requested bytes: {item['relative']}"
                                )
                            applied.append(item)
                            continue
                        _publish_exclusive(
                            state_fd,
                            transaction_fd,
                            item["relative"],
                            item["content"],
                        )
                        applied.append(item)

                    for item in prepared:
                        readback = _read_relative_bytes(state_fd, item["relative"])
                        assert readback is not None
                        if _sha256(readback) != item["content_sha256"]:
                            raise WriterError(
                                f"Readback hash mismatch: {item['relative']}"
                            )

                    receipt = {
                        "receipt_version": RECEIPT_VERSION,
                        "status": "completed",
                        "project_id": request["project_id"],
                        "project_profile_revision": request[
                            "project_profile_revision"
                        ],
                        "run_id": request["run_id"],
                        "idempotency_key": request["idempotency_key"],
                        "requested_by": request["requested_by"],
                        "request_sha256": request_sha256,
                        "writes": [
                            {
                                "path": item["relative"].as_posix(),
                                "mode": "create",
                                "sha256": item["content_sha256"],
                                "bytes": len(item["content"]),
                                "media_type": item["media_type"],
                            }
                            for item in prepared
                        ],
                        "created_at": dt.datetime.now(dt.timezone.utc)
                        .replace(microsecond=0)
                        .isoformat()
                        .replace("+00:00", "Z"),
                    }
                    receipt_bytes = json.dumps(
                        receipt,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ).encode("utf-8") + b"\n"
                    _publish_exclusive(
                        state_fd,
                        transaction_fd,
                        receipt_relative,
                        receipt_bytes,
                    )
                    receipt_readback = _read_relative_bytes(
                        state_fd,
                        receipt_relative,
                    )
                    if receipt_readback != receipt_bytes:
                        raise WriterError("Receipt readback mismatch")
                    return receipt
                except Exception as exc:
                    if applied:
                        raise WriterError(
                            "Request incomplete after "
                            f"{len(applied)} immutable write(s); no completed receipt: "
                            f"{exc}"
                        ) from exc
                    raise
            finally:
                os.close(transaction_fd)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--project-config", required=True, type=Path)
    parser.add_argument("request", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        receipt = apply_request(args.workspace, args.project_config, args.request)
    except WriterError as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}), file=sys.stderr)
        return 2
    except OSError as exc:
        print(
            json.dumps({"status": "failed", "error": f"filesystem: {exc}"}),
            file=sys.stderr,
        )
        return 3
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
