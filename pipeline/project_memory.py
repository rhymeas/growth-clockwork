#!/usr/bin/env python3
"""Project-scoped, evidence-backed memory for the modular Growth OS.

Canonical memory is one immutable JSON record per fact/decision/learning. The
module deliberately does not store chats, hidden model state, embeddings, or
silent summaries. Search is a deterministic lexical view over those records and
can be replaced by a rebuildable SQLite FTS5 cache without changing the record
contract.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tempfile
from typing import Any, Iterable
import uuid

from pipeline import root_writer
from pipeline.root_writer import WriterError
from pipeline.validate_json_suites import (
    SuiteConfigurationError,
    load_registered_schema,
    validate_registered_instance,
)


SCHEMA_ID = "project-memory-record@1"
TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
FTS_INDEX_VERSION = "1.0"


class ProjectMemoryError(ValueError):
    """Raised when a memory record or its evidence boundary is invalid."""


def _canonical_json(value: Any) -> bytes:
    try:
        return (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError) as exc:
        raise ProjectMemoryError("Memory record must be UTF-8 JSON") from exc


def _parse_rfc3339(value: str, label: str) -> dt.datetime:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = dt.datetime.fromisoformat(candidate)
    except (TypeError, ValueError) as exc:
        raise ProjectMemoryError(f"{label} must be an RFC3339 date-time") from exc
    if parsed.tzinfo is None:
        raise ProjectMemoryError(f"{label} must include a timezone")
    return parsed


def _workspace_for_profile(profile_path: Path) -> Path:
    resolved = profile_path.resolve(strict=True)
    if resolved.name != "project.json" or resolved.parent.parent.name != "projects":
        raise ProjectMemoryError(
            "Project profile must live at <workspace>/projects/<profile>/project.json"
        )
    return resolved.parents[2]


def _profile_state(
    profile_path: Path,
) -> tuple[Path, dict[str, Any], Path]:
    workspace = _workspace_for_profile(profile_path)
    try:
        profile = root_writer.load_project_profile(profile_path)
    except WriterError as exc:
        raise ProjectMemoryError(str(exc)) from exc
    state_root = workspace / profile["_state_root"]
    return workspace, profile, state_root


def _safe_state_file(
    workspace: Path, profile: dict[str, Any], raw_ref: str
) -> Path:
    try:
        relative = root_writer._validate_relative_path(raw_ref, "artifact_ref")
        path = root_writer._assert_no_symlink_path(
            workspace, profile["_state_root"] / relative
        )
    except WriterError as exc:
        raise ProjectMemoryError(str(exc)) from exc
    try:
        path.relative_to(workspace / profile["_state_root"])
    except ValueError as exc:
        raise ProjectMemoryError("Evidence reference escapes project state") from exc
    if not path.is_file():
        raise ProjectMemoryError(f"Evidence artifact does not exist: {raw_ref}")
    return path


def validate_memory_record(
    profile_path: Path,
    record: dict[str, Any],
    *,
    verify_evidence: bool = True,
) -> None:
    """Validate a new record against the current profile and exact evidence."""

    try:
        errors = validate_registered_instance(profile_path, SCHEMA_ID, record)
    except SuiteConfigurationError as exc:
        raise ProjectMemoryError(str(exc)) from exc
    if errors:
        first = errors[0]
        raise ProjectMemoryError(
            f"Memory schema rejected {first.instance_path}: {first.message}"
        )

    if record["memory_id"] in set(record["supersedes"]):
        raise ProjectMemoryError("A memory record cannot supersede itself")
    evidence_refs = [item["artifact_ref"] for item in record["evidence"]]
    if len(set(evidence_refs)) != len(evidence_refs):
        raise ProjectMemoryError("Memory evidence artifact_ref values must be unique")

    valid_from = _parse_rfc3339(record["valid_from"], "valid_from")
    created_at = _parse_rfc3339(record["created_at"], "created_at")
    if valid_from > created_at:
        raise ProjectMemoryError("valid_from cannot be later than created_at")
    if record["expires_at"] is not None:
        expires_at = _parse_rfc3339(record["expires_at"], "expires_at")
        if expires_at <= valid_from:
            raise ProjectMemoryError("expires_at must be later than valid_from")

    if not verify_evidence:
        return
    workspace, profile, _ = _profile_state(profile_path)
    for item in record["evidence"]:
        path = _safe_state_file(workspace, profile, item["artifact_ref"])
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != item["artifact_sha256"]:
            raise ProjectMemoryError(
                f"Evidence hash mismatch: {item['artifact_ref']}"
            )


def memory_record_path(record: dict[str, Any]) -> PurePosixPath:
    created = _parse_rfc3339(record["created_at"], "created_at")
    return PurePosixPath(
        "memory",
        "records",
        f"{created.year:04d}",
        f"{created.month:02d}",
        f"{record['memory_id']}.{record['memory_revision']}.json",
    )


def build_memory_write_request(
    profile_path: Path,
    record: dict[str, Any],
    *,
    run_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Build the single-writer request for an immutable memory record."""

    validate_memory_record(profile_path, record, verify_evidence=True)
    _, profile, _ = _profile_state(profile_path)
    content = _canonical_json(record)
    return {
        "writer_request_version": "1.0",
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "run_id": run_id,
        "idempotency_key": idempotency_key,
        "requested_by": profile["root_role_id"],
        "writes": [
            {
                "path": memory_record_path(record).as_posix(),
                "mode": "create",
                "content": content.decode("utf-8"),
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "expected_sha256": None,
                "media_type": "application/json",
            }
        ],
    }


def append_memory_record(
    workspace_path: Path,
    profile_path: Path,
    record: dict[str, Any],
    *,
    run_id: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Persist one record through the immutable Root Writer."""

    key = idempotency_key or str(uuid.uuid4())
    request = build_memory_write_request(
        profile_path, record, run_id=run_id, idempotency_key=key
    )
    with tempfile.TemporaryDirectory(prefix="growth-memory-") as temporary:
        request_path = Path(temporary) / "request.json"
        request_path.write_bytes(_canonical_json(request))
        try:
            return root_writer.apply_request(
                workspace_path.resolve(), profile_path.resolve(), request_path
            )
        except WriterError as exc:
            raise ProjectMemoryError(str(exc)) from exc


def _load_record_bytes(
    profile_path: Path,
) -> list[tuple[PurePosixPath, bytes, dict[str, Any]]]:
    workspace, profile, state_root = _profile_state(profile_path)
    root = state_root / "memory" / "records"
    if not root.exists():
        return []
    if root.is_symlink() or not root.is_dir():
        raise ProjectMemoryError("Memory record root is not a safe directory")

    try:
        registered = load_registered_schema(profile_path, SCHEMA_ID)
    except SuiteConfigurationError as exc:
        raise ProjectMemoryError(str(exc)) from exc

    loaded: list[tuple[PurePosixPath, bytes, dict[str, Any]]] = []
    seen_ids: set[str] = set()
    for path in sorted(root.rglob("*.json")):
        relative_state = PurePosixPath(path.relative_to(state_root).as_posix())
        try:
            safe = root_writer._assert_no_symlink_path(
                workspace, profile["_state_root"] / relative_state
            )
        except WriterError as exc:
            raise ProjectMemoryError(str(exc)) from exc
        if safe != path or not safe.is_file():
            raise ProjectMemoryError(f"Unsafe memory record path: {relative_state}")
        content = safe.read_bytes()
        try:
            value = json.loads(content.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ProjectMemoryError(
                f"Invalid memory JSON at {relative_state}: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise ProjectMemoryError(f"Memory record is not an object: {relative_state}")
        if value.get("project_id") != registered.project_id:
            raise ProjectMemoryError(f"Cross-project memory record: {relative_state}")
        errors = registered.validator.validate(value)
        if errors:
            first = errors[0]
            raise ProjectMemoryError(
                f"Invalid memory record {relative_state} at {first.instance_path}: "
                f"{first.message}"
            )
        memory_id = value["memory_id"]
        if memory_id in seen_ids:
            raise ProjectMemoryError(f"Duplicate memory_id: {memory_id}")
        seen_ids.add(memory_id)
        for evidence in value["evidence"]:
            evidence_path = _safe_state_file(
                workspace, profile, evidence["artifact_ref"]
            )
            if hashlib.sha256(evidence_path.read_bytes()).hexdigest() != evidence[
                "artifact_sha256"
            ]:
                raise ProjectMemoryError(
                    f"Evidence changed for memory record {memory_id}"
                )
        loaded.append((relative_state, content, value))
    return loaded


def _tokens(values: Iterable[str]) -> set[str]:
    return {
        token.casefold()
        for value in values
        for token in TOKEN_RE.findall(value)
        if token
    }


def _memory_source_revision(
    loaded: Iterable[tuple[PurePosixPath, bytes, dict[str, Any]]]
) -> str:
    digest = hashlib.sha256()
    for relative, content, _ in loaded:
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def default_memory_index_path(profile_path: Path) -> Path:
    workspace, profile, _ = _profile_state(profile_path)
    return (
        workspace
        / ".growth-clockwork-cache"
        / profile["project_id"]
        / profile["profile_revision"]
        / "memory-fts5.sqlite3"
    )


def rebuild_memory_index(
    profile_path: Path,
    *,
    index_path: Path | None = None,
) -> dict[str, Any]:
    """Rebuild one disposable SQLite FTS5 index from canonical memory records."""

    loaded = _load_record_bytes(profile_path)
    _, profile, _ = _profile_state(profile_path)
    target = (index_path or default_memory_index_path(profile_path)).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="memory-fts5-", suffix=".sqlite3.tmp", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    superseded = {
        old_id
        for _, _, record in loaded
        for old_id in record.get("supersedes", [])
    }
    source_revision = _memory_source_revision(loaded)
    try:
        connection = sqlite3.connect(temporary)
        try:
            connection.executescript(
                """
                PRAGMA journal_mode=DELETE;
                PRAGMA synchronous=FULL;
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE records (
                  memory_id TEXT PRIMARY KEY,
                  memory_type TEXT NOT NULL,
                  claim_mode TEXT NOT NULL,
                  evidence_grade TEXT NOT NULL,
                  decision_eligible INTEGER NOT NULL,
                  statement TEXT NOT NULL,
                  tags TEXT NOT NULL,
                  record_ref TEXT NOT NULL,
                  record_sha256 TEXT NOT NULL,
                  source_profile_revision TEXT NOT NULL,
                  expires_at TEXT,
                  superseded INTEGER NOT NULL
                );
                CREATE VIRTUAL TABLE records_fts USING fts5(
                  memory_id UNINDEXED,
                  statement,
                  tags,
                  memory_type,
                  claim_mode,
                  tokenize='unicode61'
                );
                """
            )
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                [
                    ("index_version", FTS_INDEX_VERSION),
                    ("project_id", profile["project_id"]),
                    ("project_profile_revision", profile["profile_revision"]),
                    ("source_revision", source_revision),
                ],
            )
            for relative, content, record in loaded:
                values = (
                    record["memory_id"],
                    record["memory_type"],
                    record["claim_mode"],
                    record["evidence_grade"],
                    int(record["decision_eligible"]),
                    record["statement"],
                    " ".join(record["tags"]),
                    relative.as_posix(),
                    hashlib.sha256(content).hexdigest(),
                    record["project_profile_revision"],
                    record["expires_at"],
                    int(record["memory_id"] in superseded),
                )
                connection.execute(
                    "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    values,
                )
                connection.execute(
                    "INSERT INTO records_fts VALUES (?, ?, ?, ?, ?)",
                    (
                        record["memory_id"],
                        record["statement"],
                        " ".join(record["tags"]),
                        record["memory_type"],
                        record["claim_mode"],
                    ),
                )
            connection.commit()
        finally:
            connection.close()
        os.replace(temporary, target)
    except (OSError, sqlite3.Error) as exc:
        temporary.unlink(missing_ok=True)
        raise ProjectMemoryError(f"Cannot rebuild SQLite FTS5 index: {exc}") from exc
    return {
        "index_version": FTS_INDEX_VERSION,
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "source_revision": source_revision,
        "record_count": len(loaded),
        "index_path": str(target),
        "canonical": False,
        "rebuildable": True,
    }


def query_project_memory_fts(
    profile_path: Path,
    query: str,
    *,
    index_path: Path | None = None,
    memory_types: set[str] | None = None,
    include_exploratory: bool = False,
    limit: int = 20,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Query a fresh project-bound FTS5 cache and preserve canonical pointers."""

    if not isinstance(query, str) or not query.strip():
        raise ProjectMemoryError("query must be a non-empty string")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ProjectMemoryError("limit must be an integer from 1 to 100")
    query_tokens = sorted(_tokens([query]))
    if not query_tokens:
        raise ProjectMemoryError("query must contain searchable characters")
    current_time = now or dt.datetime.now(dt.timezone.utc)
    if current_time.tzinfo is None:
        raise ProjectMemoryError("now must include a timezone")

    loaded = _load_record_bytes(profile_path)
    _, profile, _ = _profile_state(profile_path)
    target = (index_path or default_memory_index_path(profile_path)).resolve()
    if not target.is_file():
        raise ProjectMemoryError("Memory FTS5 index does not exist; rebuild it first")
    expected_meta = {
        "index_version": FTS_INDEX_VERSION,
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "source_revision": _memory_source_revision(loaded),
    }
    expression = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in query_tokens)
    try:
        connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            meta = {
                row["key"]: row["value"]
                for row in connection.execute("SELECT key, value FROM metadata")
            }
            if meta != expected_meta:
                raise ProjectMemoryError(
                    "Memory FTS5 index is stale or belongs to another project revision"
                )
            rows = connection.execute(
                """
                SELECT records.*, bm25(records_fts, 0.0, 2.0, 4.0, 1.0, 1.0) AS rank
                FROM records_fts
                JOIN records USING (memory_id)
                WHERE records_fts MATCH ? AND records.superseded = 0
                ORDER BY rank, records.memory_id
                """,
                (expression,),
            ).fetchall()
        finally:
            connection.close()
    except ProjectMemoryError:
        raise
    except sqlite3.Error as exc:
        raise ProjectMemoryError(f"Cannot query SQLite FTS5 index: {exc}") from exc

    matched: list[dict[str, Any]] = []
    for row in rows:
        if memory_types is not None and row["memory_type"] not in memory_types:
            continue
        if not include_exploratory and (
            row["evidence_grade"] == "exploratory" or not row["decision_eligible"]
        ):
            continue
        if row["expires_at"] is not None and _parse_rfc3339(
            row["expires_at"], "expires_at"
        ) <= current_time:
            continue
        matched.append(
            {
                "memory_id": row["memory_id"],
                "memory_type": row["memory_type"],
                "claim_mode": row["claim_mode"],
                "evidence_grade": row["evidence_grade"],
                "decision_eligible": bool(row["decision_eligible"]),
                "statement": row["statement"],
                "record_ref": row["record_ref"],
                "record_sha256": row["record_sha256"],
                "source_profile_revision": row["source_profile_revision"],
                "score": round(-float(row["rank"]), 12),
            }
        )
        if len(matched) >= limit:
            break
    return {
        "query_version": "1.0",
        "index_version": FTS_INDEX_VERSION,
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "source_revision": expected_meta["source_revision"],
        "query": query,
        "include_exploratory": include_exploratory,
        "matched": matched,
        "canonical": False,
    }


def query_project_memory(
    profile_path: Path,
    query: str,
    *,
    memory_types: set[str] | None = None,
    include_exploratory: bool = False,
    limit: int = 20,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Return deterministic matches with exact record hashes.

    Older project-profile revisions remain readable. The record schema and
    ``project_id`` are revalidated, while the original profile revision stays as
    provenance instead of being rewritten.
    """

    if not isinstance(query, str) or not query.strip():
        raise ProjectMemoryError("query must be a non-empty string")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ProjectMemoryError("limit must be an integer from 1 to 100")
    query_tokens = _tokens([query])
    if not query_tokens:
        raise ProjectMemoryError("query must contain searchable characters")

    loaded = _load_record_bytes(profile_path)
    superseded = {
        old_id
        for _, _, record in loaded
        for old_id in record.get("supersedes", [])
    }
    current_time = now or dt.datetime.now(dt.timezone.utc)
    if current_time.tzinfo is None:
        raise ProjectMemoryError("now must include a timezone")

    matches: list[dict[str, Any]] = []
    for relative, content, record in loaded:
        if record["memory_id"] in superseded:
            continue
        if memory_types is not None and record["memory_type"] not in memory_types:
            continue
        if record["evidence_grade"] == "exploratory" and not include_exploratory:
            continue
        if not record["decision_eligible"] and not include_exploratory:
            continue
        if record["expires_at"] is not None and _parse_rfc3339(
            record["expires_at"], "expires_at"
        ) <= current_time:
            continue

        statement_tokens = _tokens([record["statement"]])
        tag_tokens = _tokens(record["tags"])
        type_tokens = _tokens([record["memory_type"], record["claim_mode"]])
        score = (
            4 * len(query_tokens & tag_tokens)
            + 2 * len(query_tokens & statement_tokens)
            + len(query_tokens & type_tokens)
        )
        if score < 1:
            continue
        matches.append(
            {
                "memory_id": record["memory_id"],
                "memory_type": record["memory_type"],
                "claim_mode": record["claim_mode"],
                "evidence_grade": record["evidence_grade"],
                "decision_eligible": record["decision_eligible"],
                "statement": record["statement"],
                "record_ref": relative.as_posix(),
                "record_sha256": hashlib.sha256(content).hexdigest(),
                "source_profile_revision": record["project_profile_revision"],
                "score": score,
            }
        )
    matches.sort(
        key=lambda item: (-item["score"], item["memory_id"], item["record_ref"])
    )
    profile = root_writer.load_project_profile(profile_path)
    return {
        "query_version": "1.0",
        "project_id": profile["project_id"],
        "project_profile_revision": profile["profile_revision"],
        "query": query,
        "include_exploratory": include_exploratory,
        "matched": matches[:limit],
        "total_active_records": len(loaded) - len(superseded),
    }
