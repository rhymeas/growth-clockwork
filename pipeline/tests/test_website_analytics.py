from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from pipeline.website_analytics import AnalyticsReportError, read_platform_report, read_report


def report(project_id: str = "alpha") -> dict[str, object]:
    return {
        "report_version": "1.0",
        "project_id": project_id,
        "source": "ga4",
        "property_id_hash": "a" * 64,
        "fetched_at": "2026-09-07T10:00:00Z",
        "window": {
            "start_date": "2026-08-10",
            "end_date": "2026-09-05",
            "timezone": "America/Vancouver",
            "excluded_latest_days": 2,
        },
        "filters": {"internal_test_traffic_excluded": True},
        "metrics": {"active_users": 12, "engaged_sessions": 7, "download_clicks": 0},
    }


class WebsiteAnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        (self.workspace / "runtime/analytics").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, value: object) -> None:
        (self.workspace / "runtime/analytics/alpha.json").write_text(
            json.dumps(value), encoding="utf-8"
        )

    def test_missing_report_is_not_connected_not_zero(self) -> None:
        value = read_report(self.workspace, "alpha")
        self.assertEqual(value, {"project_id": "alpha", "status": "not_connected", "source": "ga4", "report": None})

    def test_verified_report_preserves_real_zero_and_hides_property(self) -> None:
        self.write(report())
        value = read_report(self.workspace, "alpha")
        self.assertEqual(value["status"], "verified")
        self.assertEqual(value["report"]["metrics"]["download_clicks"], 0)
        self.assertNotIn("property_id_hash", value["report"])
        self.assertRegex(value["report"]["report_sha256"], r"^[0-9a-f]{64}$")

    def test_wrong_project_or_unexcluded_test_traffic_fails_closed(self) -> None:
        self.write(report("beta"))
        with self.assertRaises(AnalyticsReportError):
            read_report(self.workspace, "alpha")
        unsafe = report()
        unsafe["filters"]["internal_test_traffic_excluded"] = False
        self.write(unsafe)
        with self.assertRaises(AnalyticsReportError):
            read_report(self.workspace, "alpha")

    def test_native_platform_reports_keep_their_own_metric_names_and_windows(self) -> None:
        value = {
            "report_version": "1.0",
            "project_id": "alpha",
            "source": "native_platform_analytics",
            "fetched_at": "2026-09-07T10:00:00Z",
            "platforms": [{
                "platform": "youtube",
                "account_id_hash": "b" * 64,
                "window": {"start_date": "2026-09-01", "end_date": "2026-09-06", "timezone": "UTC"},
                "metrics": [
                    {"metric_id": "views", "label": "Views", "value": 0, "unit": "count"},
                    {"metric_id": "watch-time", "label": "Watch time", "value": None, "unit": "seconds"},
                ],
            }],
        }
        (self.workspace / "runtime/analytics/alpha-platforms.json").write_text(json.dumps(value), encoding="utf-8")
        result = read_platform_report(self.workspace, "alpha")
        self.assertEqual(result["report"]["platforms"][0]["metrics"][0]["value"], 0)
        self.assertNotIn("account_id_hash", result["report"]["platforms"][0])

    def test_native_platform_reports_reject_duplicates_and_invalid_percentages(self) -> None:
        base = {
            "platform": "instagram", "account_id_hash": "b" * 64,
            "window": {"start_date": "2026-09-01", "end_date": "2026-09-06", "timezone": "UTC"},
            "metrics": [{"metric_id": "reach", "label": "Reach", "value": 2, "unit": "count"}],
        }
        value = {"report_version": "1.0", "project_id": "alpha", "source": "native_platform_analytics", "fetched_at": "2026-09-07T10:00:00Z", "platforms": [base, dict(base)]}
        (self.workspace / "runtime/analytics/alpha-platforms.json").write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(AnalyticsReportError):
            read_platform_report(self.workspace, "alpha")
        value["platforms"] = [dict(base, metrics=[{"metric_id": "rate", "label": "Rate", "value": 101, "unit": "percent"}])]
        (self.workspace / "runtime/analytics/alpha-platforms.json").write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(AnalyticsReportError):
            read_platform_report(self.workspace, "alpha")
    def test_unknown_fields_negative_values_and_symlinks_fail_closed(self) -> None:
        extra = report()
        extra["estimate"] = 99
        self.write(extra)
        with self.assertRaises(AnalyticsReportError):
            read_report(self.workspace, "alpha")
        negative = report()
        negative["metrics"]["active_users"] = -1
        self.write(negative)
        with self.assertRaises(AnalyticsReportError):
            read_report(self.workspace, "alpha")
        target = self.workspace / "real.json"
        target.write_text(json.dumps(report()), encoding="utf-8")
        path = self.workspace / "runtime/analytics/alpha.json"
        path.unlink()
        path.symlink_to(target)
        with self.assertRaises(AnalyticsReportError):
            read_report(self.workspace, "alpha")


if __name__ == "__main__":
    unittest.main()
