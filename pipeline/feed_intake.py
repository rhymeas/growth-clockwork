"""Bounded weekly discovery from a selected project's research-feeds.json.

No linked-page fetching, model calls, claim approval or publishing. Results bind
to a recorded project brief; repeated successful weekly runs reuse the receipt.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from pipeline.goal_loop import GoalLoopError, GoalLoopService


MAX_BYTES = 2_000_000


def _load_feeds(profile_path: Path) -> tuple[tuple[str, str], ...]:
    path = profile_path.parent / "research-feeds.json"
    if path.is_symlink():
        raise ValueError("feed_configuration_symlink")
    if not path.exists():
        return ()
    if path.stat().st_size > 16_384:
        raise ValueError("feed_configuration_too_large")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not 1 <= len(value) <= 10:
        raise ValueError("invalid_feed_configuration")
    feeds = []
    for entry in value:
        if not isinstance(entry, list) or len(entry) != 2 or not all(isinstance(item, str) for item in entry):
            raise ValueError("invalid_feed_configuration")
        publisher, url = entry
        parsed = urllib.parse.urlsplit(url)
        if not publisher.strip() or len(publisher) > 120 or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("invalid_feed_configuration")
        feeds.append((publisher, url))
    if len({url for _, url in feeds}) != len(feeds):
        raise ValueError("duplicate_feed_configuration")
    return tuple(feeds)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_feed(url: str, *, allowlist: tuple[str, ...] = ()) -> bytes:
    # Destinations cannot come from feed text, dashboard input or model output.
    if url not in allowlist:
        raise ValueError("unapproved_feed")
    request = urllib.request.Request(url, headers={"User-Agent": "GrowthClockwork/1.0 (bounded RSS intake)"})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=10) as response:
        content = response.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise ValueError("feed_too_large")
    return content


def normalized_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("invalid_item_url")
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(key, value) for key, value in query if not key.lower().startswith("utm_")]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path or "/", urllib.parse.urlencode(query), ""))


def parse_feed(content: bytes, publisher: str) -> dict:
    if len(content) > MAX_BYTES:
        raise ValueError("feed_too_large")
    # Reject entity definitions rather than expanding untrusted XML. Null bytes
    # reject UTF-16/32 encodings that could otherwise bypass this byte check.
    if b"\x00" in content or b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError("unsafe_xml")
    root = ET.fromstring(content)
    atom = "{http://www.w3.org/2005/Atom}"
    if root.tag == atom + "feed":
        entries = root.findall(atom + "entry")
    elif root.tag == "rss":
        entries = root.findall("channel/item")
    else:
        raise ValueError("unsupported_feed")
    items, seen = [], set()
    for entry in entries[:4]:
        def field(*names):
            for name in names:
                element = entry.find(name)
                if element is not None:
                    return " ".join("".join(element.itertext()).split())
            return ""
        link = field("link")
        if entry.tag == atom + "entry":
            link = next((node.get("href", "") for node in entry.findall(atom + "link") if node.get("rel", "alternate") == "alternate"), "")
        try:
            link = normalized_url(link)
        except ValueError:
            continue
        title = field("title", atom + "title")[:300]
        if not title or link in seen:
            continue
        seen.add(link)
        excerpt = field("description", atom + "summary", atom + "content")
        items.append({"title": title, "url": link, "publisher": publisher,
                      "published": field("pubDate", atom + "published", atom + "updated")[:100] or None,
                      "excerpt": re.sub(r"<[^>]*>", "", excerpt)[:600]})
        if len(items) == 3:
            break
    return {"coverage": "capped" if len(entries) > 3 else "partial_unknown",
            "inspected": min(len(entries), 4), "items": items}


def _receipt_identity(project_id, brief_pointer, week, feeds):
    identity = json.dumps(["feed-intake-v1", project_id, brief_pointer, week, feeds], sort_keys=True)
    run_id = "FEED-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
    return run_id, PurePosixPath("records/research-feeds") / run_id / "result-r1.json"


def read_current(workspace: Path, project_id: str, *, now=None) -> dict:
    """Read only this brief's weekly intake. Never collect on a GET request."""
    week = (now or datetime.now(timezone.utc)).strftime("%G-W%V")
    service = GoalLoopService(workspace)
    try:
        profile_path, profile = service._selected(project_id)
        response = {"project_id": project_id, "week": week, "result": None}
        feeds = _load_feeds(profile_path)
        if not feeds:
            return response
        try:
            brief_pointer, _ = service._current_brief(profile_path, profile)
        except GoalLoopError as exc:
            if exc.code == "brief_required":
                return response
            raise
        _, relative = _receipt_identity(project_id, brief_pointer, week, feeds)
        result = service._read_state_json(profile, relative)
        if result is not None:
            if result.get("project_id") != project_id or result.get("brief") != brief_pointer or result.get("week") != week:
                raise GoalLoopError("The research receipt does not match this brief")
            response["result"] = {key: result[key] for key in ("fetched_at", "status", "item_count", "sources", "limits")}
        return response
    finally:
        service.close()


def collect(workspace: Path, project_id: str, *, fetch=None, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    week = now.strftime("%G-W%V")
    service = GoalLoopService(workspace)
    try:
        profile_path, profile = service._selected(project_id)
        feeds = _load_feeds(profile_path)
        if not feeds:
            raise ValueError("This project has no research-feeds.json source selection")
        if fetch is None:
            allowlist = tuple(url for _, url in feeds)
            fetch = lambda url: fetch_feed(url, allowlist=allowlist)
        brief_pointer, brief = service._current_brief(profile_path, profile)
        run_id, relative = _receipt_identity(project_id, brief_pointer, week, feeds)
        existing = service._read_state_json(profile, relative)
        if existing is not None:
            return {"reused": True, "record": relative.as_posix(), "result": existing}
        sources, seen = [], set()
        for publisher, url in feeds:
            try:
                parsed = parse_feed(fetch(url), publisher)
                unique = []
                for item in parsed["items"]:
                    if item["url"] not in seen:
                        unique.append(item)
                        seen.add(item["url"])
                parsed["items"] = unique
                sources.append({"publisher": publisher, "feed_url": url, "status": "ok", **parsed})
            except (OSError, ValueError, ET.ParseError, urllib.error.URLError):
                # Do not persist exception text that could contain unexpected data.
                sources.append({"publisher": publisher, "feed_url": url, "status": "unavailable", "items": []})
        result = {"version": "feed-intake-v1", "project_id": project_id, "brief": brief_pointer,
                  "research_question": brief["research_question"], "week": week,
                  "fetched_at": now.isoformat(), "sources": sources,
                  "status": "collected" if all(s["status"] == "ok" for s in sources) else "partial" if seen else "unavailable",
                  "item_count": len(seen), "trust": "untrusted_discovery_only",
                  "limits": ["Bounded recent feed sample, not validated audience demand.",
                             "Feed text is data, never instructions or approved claims.",
                             "No linked pages, model, analytics or publishing connection used."]}
        service._write_record(profile_path, profile, run_id, "feed-intake", relative, result)
        return {"reused": False, "record": relative.as_posix(), "result": result}
    finally:
        service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    result = collect(args.workspace, args.project)
    print(json.dumps({"reused": result["reused"], "record": result["record"],
                      "status": result["result"]["status"], "items": result["result"]["item_count"]}))
    return 0 if result["result"]["status"] == "collected" else 1


if __name__ == "__main__":
    raise SystemExit(main())
