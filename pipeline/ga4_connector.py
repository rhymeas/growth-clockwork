#!/usr/bin/env python3
"""Fetch one bounded GA4 report into Growth Clockwork's private runtime.

Authentication is supplied as a short-lived OAuth bearer token through the
process environment. The token and raw property identifier never enter reports,
logs, broker state, project files or the dashboard.
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
import uuid

from pipeline.website_analytics import read_report


CONFIG_FIELDS = {
    "enabled", "project_id", "property_id", "property_timezone", "window_days",
    "excluded_latest_days", "download_event_name", "internal_test_traffic_excluded",
}
PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PROPERTY_ID_RE = re.compile(r"^[0-9]{1,30}$")
EVENT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
MAX_CONFIG_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
GA4_ORIGIN = "https://analyticsdata.googleapis.com"


class GA4ConnectorError(ValueError):
    pass


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _read_config(workspace: Path) -> dict[str, Any]:
    path = workspace / "runtime/ga4.json"
    try:
        info = path.stat()
        if (path.is_symlink() or not path.is_file() or info.st_size > MAX_CONFIG_BYTES
                or (os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077)):
            raise ValueError()
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(value, dict) or set(value) != CONFIG_FIELDS:
            raise ValueError()
        return value
    except (OSError, UnicodeError, ValueError, TypeError):
        raise GA4ConnectorError("GA4 configuration is missing, invalid or unsafe") from None


def _configuration(workspace: Path) -> dict[str, Any]:
    value = _read_config(workspace)
    if value["enabled"] is not True:
        raise GA4ConnectorError("GA4 connector is not enabled")
    if not PROJECT_ID_RE.fullmatch(value["project_id"]):
        raise GA4ConnectorError("GA4 project id is invalid")
    if not isinstance(value["property_id"], str) or not PROPERTY_ID_RE.fullmatch(value["property_id"]):
        raise GA4ConnectorError("GA4 property id is invalid")
    if (not isinstance(value["property_timezone"], str)
            or not 1 <= len(value["property_timezone"].strip()) <= 80):
        raise GA4ConnectorError("GA4 property timezone is invalid")
    if (isinstance(value["window_days"], bool) or not isinstance(value["window_days"], int)
            or not 1 <= value["window_days"] <= 366):
        raise GA4ConnectorError("GA4 window is invalid")
    if (isinstance(value["excluded_latest_days"], bool)
            or not isinstance(value["excluded_latest_days"], int)
            or not 0 <= value["excluded_latest_days"] <= 31):
        raise GA4ConnectorError("GA4 excluded-day count is invalid")
    if (not isinstance(value["download_event_name"], str)
            or not EVENT_NAME_RE.fullmatch(value["download_event_name"])):
        raise GA4ConnectorError("GA4 download event name is invalid")
    if value["internal_test_traffic_excluded"] is not True:
        raise GA4ConnectorError("Verify the GA4 internal-traffic filter before enabling reports")
    return value


def readiness(
    workspace: Path, project_id: str, *, environment: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return a credential-free setup state for Project Desk."""
    if not PROJECT_ID_RE.fullmatch(project_id):
        return {"state": "configuration_required"}
    try:
        value = _read_config(Path(workspace))
    except GA4ConnectorError:
        return {"state": "configuration_required"}
    if value.get("project_id") != project_id:
        return {"state": "configuration_required"}
    if value.get("enabled") is not True:
        return {"state": "disabled"}
    try:
        _configuration(Path(workspace))
    except GA4ConnectorError:
        return {"state": "configuration_required"}
    token = (os.environ if environment is None else environment).get("GROWTH_GA4_ACCESS_TOKEN")
    if (not isinstance(token, str) or not 16 <= len(token) <= 4096
            or any(character.isspace() for character in token)):
        return {"state": "authentication_required"}
    return {"state": "ready"}


def _post_json(url: str, access_token: str, payload: bytes) -> dict[str, Any]:
    outbound = request.Request(
        url, data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
    )
    try:
        with request.build_opener(_NoRedirect).open(outbound, timeout=30) as response:
            content = response.read(MAX_RESPONSE_BYTES + 1)
            if len(content) > MAX_RESPONSE_BYTES:
                raise GA4ConnectorError("GA4 response exceeds its size limit")
            value = json.loads(content)
            if not isinstance(value, dict):
                raise ValueError()
            return value
    except GA4ConnectorError:
        raise
    except (error.HTTPError, error.URLError, TimeoutError, OSError, UnicodeError, ValueError):
        raise GA4ConnectorError("GA4 request failed without updating the local report") from None


def _metric_total(value: dict[str, Any], expected_count: int) -> list[int]:
    try:
        totals = value["totals"]
        metric_values = totals[0]["metricValues"]
        if len(totals) != 1 or len(metric_values) != expected_count:
            raise ValueError()
        result = []
        for metric in metric_values:
            raw = metric["value"]
            if not isinstance(raw, str) or not raw.isdigit():
                raise ValueError()
            result.append(int(raw))
        return result
    except (KeyError, IndexError, TypeError, ValueError):
        raise GA4ConnectorError("GA4 returned an incompatible metric response") from None


def _query(date_range: dict[str, str], metrics: list[str], *, event_name: str | None = None) -> bytes:
    value: dict[str, Any] = {
        "dateRanges": [date_range],
        "metrics": [{"name": name} for name in metrics],
        "metricAggregations": ["TOTAL"],
        "keepEmptyRows": True,
    }
    if event_name is not None:
        value["dimensionFilter"] = {"filter": {
            "fieldName": "eventName",
            "stringFilter": {
                "matchType": "EXACT", "value": event_name, "caseSensitive": True,
            },
        }}
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _write_report(workspace: Path, project_id: str, value: dict[str, Any]) -> Path:
    runtime = workspace / "runtime"
    if runtime.is_symlink() or not runtime.is_dir():
        raise GA4ConnectorError("Runtime directory is unavailable")
    directory = runtime / "analytics"
    try:
        if directory.exists():
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError()
        else:
            directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)
        target = directory / f"{project_id}.json"
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError()
        content = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
        temporary = directory / f".{project_id}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written < 1:
                    raise OSError("incomplete report write")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, target)
        os.chmod(target, 0o600)
        return target
    except (OSError, ValueError):
        try:
            temporary.unlink(missing_ok=True)
        except (OSError, UnboundLocalError):
            pass
        raise GA4ConnectorError("GA4 report could not be stored safely") from None


def fetch_report(
    workspace: Path, *, environment: dict[str, str] | None = None,
    today: dt.date | None = None,
    transport: Callable[[str, str, bytes], dict[str, Any]] = _post_json,
) -> dict[str, Any]:
    workspace = Path(workspace).resolve()
    config = _configuration(workspace)
    token = (os.environ if environment is None else environment).get("GROWTH_GA4_ACCESS_TOKEN")
    if (not isinstance(token, str) or not 16 <= len(token) <= 4096
            or any(character.isspace() for character in token)):
        raise GA4ConnectorError("GROWTH_GA4_ACCESS_TOKEN is unavailable or invalid")
    end = (today or dt.datetime.now(dt.timezone.utc).date()) - dt.timedelta(
        days=config["excluded_latest_days"])
    start = end - dt.timedelta(days=config["window_days"] - 1)
    date_range = {"startDate": start.isoformat(), "endDate": end.isoformat()}
    url = f"{GA4_ORIGIN}/v1beta/properties/{config['property_id']}:runReport"
    overview = transport(url, token, _query(date_range, ["activeUsers", "engagedSessions"]))
    events = transport(
        url, token,
        _query(date_range, ["eventCount"], event_name=config["download_event_name"]),
    )
    active_users, engaged_sessions = _metric_total(overview, 2)
    download_clicks = _metric_total(events, 1)[0]
    fetched_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    report = {
        "report_version": "1.0",
        "project_id": config["project_id"],
        "source": "ga4",
        "property_id_hash": hashlib.sha256(config["property_id"].encode("ascii")).hexdigest(),
        "fetched_at": fetched_at,
        "window": {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "timezone": config["property_timezone"],
            "excluded_latest_days": config["excluded_latest_days"],
        },
        "filters": {"internal_test_traffic_excluded": True},
        "metrics": {
            "active_users": active_users,
            "engaged_sessions": engaged_sessions,
            "download_clicks": download_clicks,
        },
    }
    _write_report(workspace, config["project_id"], report)
    return read_report(workspace, config["project_id"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = fetch_report(args.workspace)
    except GA4ConnectorError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
