"""Strict read boundary for a local GA4 website report.

The future OAuth fetcher writes one project-scoped report below ``runtime/analytics``.
This module never contacts Google and never reads credentials. Missing data stays
distinct from a verified zero.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any


REPORT_VERSION = "1.0"
MAX_REPORT_BYTES = 64 * 1024
PLATFORMS = ("youtube", "instagram", "tiktok", "x", "pinterest")
PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RFC3339_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class AnalyticsReportError(ValueError):
    """A malformed or unsafe local analytics report."""


def _keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise AnalyticsReportError(f"{label} fields are invalid")


def _date(value: Any, label: str) -> str:
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise AnalyticsReportError(f"{label} must be an ISO date")
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise AnalyticsReportError(f"{label} must be a real date") from exc
    return value


def _metric(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AnalyticsReportError(f"{label} must be a non-negative integer or null")
    return value


def _read_regular_file(path: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise AnalyticsReportError("Analytics report cannot be opened safely") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise AnalyticsReportError("Analytics report must be a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            content = source.read(MAX_REPORT_BYTES + 1)
        if len(content) > MAX_REPORT_BYTES:
            raise AnalyticsReportError("Analytics report exceeds its size limit")
        return content
    finally:
        os.close(descriptor)


def read_report(workspace: Path, project_id: str) -> dict[str, Any]:
    """Return a safe API projection or an explicit not-connected state."""

    if not PROJECT_ID_RE.fullmatch(project_id):
        raise AnalyticsReportError("Project id is invalid")
    report_path = workspace / "runtime" / "analytics" / f"{project_id}.json"
    try:
        content = _read_regular_file(report_path)
    except FileNotFoundError:
        return {"project_id": project_id, "status": "not_connected", "source": "ga4", "report": None}
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AnalyticsReportError("Analytics report must be UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise AnalyticsReportError("Analytics report must be an object")
    _keys(value, {"report_version", "project_id", "source", "property_id_hash", "fetched_at", "window", "filters", "metrics"}, "report")
    if value["report_version"] != REPORT_VERSION or value["project_id"] != project_id or value["source"] != "ga4":
        raise AnalyticsReportError("Analytics report identity is invalid")
    if not isinstance(value["property_id_hash"], str) or not SHA256_RE.fullmatch(value["property_id_hash"]):
        raise AnalyticsReportError("Analytics property fingerprint is invalid")
    if not isinstance(value["fetched_at"], str) or not RFC3339_RE.fullmatch(value["fetched_at"]):
        raise AnalyticsReportError("Analytics fetch time is invalid")
    try:
        dt.datetime.strptime(value["fetched_at"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise AnalyticsReportError("Analytics fetch time is not real") from exc
    window = value["window"]
    if not isinstance(window, dict):
        raise AnalyticsReportError("Analytics window must be an object")
    _keys(window, {"start_date", "end_date", "timezone", "excluded_latest_days"}, "window")
    start = _date(window["start_date"], "start_date")
    end = _date(window["end_date"], "end_date")
    if end < start:
        raise AnalyticsReportError("Analytics window ends before it starts")
    if not isinstance(window["timezone"], str) or not 1 <= len(window["timezone"]) <= 80:
        raise AnalyticsReportError("Analytics timezone is invalid")
    excluded = window["excluded_latest_days"]
    if isinstance(excluded, bool) or not isinstance(excluded, int) or not 0 <= excluded <= 31:
        raise AnalyticsReportError("Excluded-day count is invalid")
    filters = value["filters"]
    if not isinstance(filters, dict):
        raise AnalyticsReportError("Analytics filters must be an object")
    _keys(filters, {"internal_test_traffic_excluded"}, "filters")
    if filters["internal_test_traffic_excluded"] is not True:
        raise AnalyticsReportError("Internal test traffic must be excluded")
    metrics = value["metrics"]
    if not isinstance(metrics, dict):
        raise AnalyticsReportError("Analytics metrics must be an object")
    _keys(metrics, {"active_users", "engaged_sessions", "download_clicks"}, "metrics")
    checked_metrics = {name: _metric(metrics[name], name) for name in metrics}
    return {
        "project_id": project_id,
        "status": "verified",
        "source": "ga4",
        "report": {
            "fetched_at": value["fetched_at"],
            "window": dict(window),
            "metrics": checked_metrics,
            "report_sha256": hashlib.sha256(content).hexdigest(),
        },
    }


def read_platform_report(workspace: Path, project_id: str) -> dict[str, Any]:
    """Read aggregate owner analytics without flattening platform definitions."""

    if not PROJECT_ID_RE.fullmatch(project_id):
        raise AnalyticsReportError("Project id is invalid")
    report_path = workspace / "runtime" / "analytics" / f"{project_id}-platforms.json"
    try:
        content = _read_regular_file(report_path)
    except FileNotFoundError:
        return {
            "project_id": project_id,
            "status": "not_connected",
            "source": "native_platform_analytics",
            "report": None,
        }
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AnalyticsReportError("Platform analytics report must be UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise AnalyticsReportError("Platform analytics report must be an object")
    _keys(value, {"report_version", "project_id", "source", "fetched_at", "platforms"}, "platform report")
    if (value["report_version"] != REPORT_VERSION or value["project_id"] != project_id
            or value["source"] != "native_platform_analytics"):
        raise AnalyticsReportError("Platform analytics report identity is invalid")
    if not isinstance(value["fetched_at"], str) or not RFC3339_RE.fullmatch(value["fetched_at"]):
        raise AnalyticsReportError("Platform analytics fetch time is invalid")
    try:
        dt.datetime.strptime(value["fetched_at"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise AnalyticsReportError("Platform analytics fetch time is not real") from exc
    if not isinstance(value["platforms"], list) or not 1 <= len(value["platforms"]) <= len(PLATFORMS):
        raise AnalyticsReportError("Platform analytics entries are invalid")
    seen: set[str] = set()
    checked_platforms: list[dict[str, Any]] = []
    for entry in value["platforms"]:
        if not isinstance(entry, dict):
            raise AnalyticsReportError("Platform entry must be an object")
        _keys(entry, {"platform", "account_id_hash", "window", "metrics"}, "platform entry")
        platform = entry["platform"]
        if platform not in PLATFORMS or platform in seen:
            raise AnalyticsReportError("Platform identity is invalid or duplicated")
        seen.add(platform)
        if not isinstance(entry["account_id_hash"], str) or not SHA256_RE.fullmatch(entry["account_id_hash"]):
            raise AnalyticsReportError("Platform account fingerprint is invalid")
        window = entry["window"]
        if not isinstance(window, dict):
            raise AnalyticsReportError("Platform window must be an object")
        _keys(window, {"start_date", "end_date", "timezone"}, "platform window")
        start = _date(window["start_date"], "start_date")
        end = _date(window["end_date"], "end_date")
        if end < start or not isinstance(window["timezone"], str) or not 1 <= len(window["timezone"]) <= 80:
            raise AnalyticsReportError("Platform window is invalid")
        metrics = entry["metrics"]
        if not isinstance(metrics, list) or not 1 <= len(metrics) <= 5:
            raise AnalyticsReportError("Platform metrics are invalid")
        metric_ids: set[str] = set()
        checked_metrics = []
        for metric in metrics:
            if not isinstance(metric, dict):
                raise AnalyticsReportError("Platform metric must be an object")
            _keys(metric, {"metric_id", "label", "value", "unit"}, "platform metric")
            metric_id = metric["metric_id"]
            if (not isinstance(metric_id, str) or not PROJECT_ID_RE.fullmatch(metric_id)
                    or metric_id in metric_ids):
                raise AnalyticsReportError("Platform metric identity is invalid or duplicated")
            metric_ids.add(metric_id)
            if not isinstance(metric["label"], str) or not 1 <= len(metric["label"].strip()) <= 60:
                raise AnalyticsReportError("Platform metric label is invalid")
            if metric["unit"] not in {"count", "seconds", "percent"}:
                raise AnalyticsReportError("Platform metric unit is invalid")
            raw_value = metric["value"]
            if raw_value is not None:
                if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)) or raw_value < 0:
                    raise AnalyticsReportError("Platform metric value is invalid")
                if metric["unit"] == "count" and not isinstance(raw_value, int):
                    raise AnalyticsReportError("Count metrics must be integers")
                if metric["unit"] == "percent" and raw_value > 100:
                    raise AnalyticsReportError("Percent metrics cannot exceed 100")
            checked_metrics.append(dict(metric))
        checked_platforms.append({
            "platform": platform,
            "window": dict(window),
            "metrics": checked_metrics,
        })
    return {
        "project_id": project_id,
        "status": "verified",
        "source": "native_platform_analytics",
        "report": {
            "fetched_at": value["fetched_at"],
            "platforms": checked_platforms,
            "report_sha256": hashlib.sha256(content).hexdigest(),
        },
    }
