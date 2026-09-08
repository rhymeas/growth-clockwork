from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from pipeline import ga4_connector


class GA4ConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name) / "workspace"
        (self.workspace / "runtime").mkdir(parents=True)
        self.config = {
            "enabled": True,
            "project_id": "example-project",
            "property_id": "123456789",
            "property_timezone": "America/Vancouver",
            "window_days": 28,
            "excluded_latest_days": 2,
            "download_event_name": "download_click",
            "internal_test_traffic_excluded": True,
        }
        self.write_config()

    def write_config(self) -> None:
        path = self.workspace / "runtime/ga4.json"
        path.write_text(json.dumps(self.config), encoding="utf-8")
        path.chmod(0o600)

    @staticmethod
    def response(*values: int) -> dict:
        return {"totals": [{"metricValues": [{"value": str(value)} for value in values]}]}

    def test_fetches_separate_overview_and_download_event_queries(self) -> None:
        calls: list[tuple[str, str, dict]] = []

        def transport(url: str, token: str, body: bytes) -> dict:
            calls.append((url, token, json.loads(body)))
            return self.response(12, 7) if len(calls) == 1 else self.response(0)

        result = ga4_connector.fetch_report(
            self.workspace,
            environment={"GROWTH_GA4_ACCESS_TOKEN": "test-access-token-123"},
            today=dt.date(2026, 9, 7), transport=transport,
        )
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["report"]["metrics"], {
            "active_users": 12, "engaged_sessions": 7, "download_clicks": 0,
        })
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "https://analyticsdata.googleapis.com/v1beta/properties/123456789:runReport")
        self.assertEqual(calls[0][2]["dateRanges"], [{"startDate": "2026-08-09", "endDate": "2026-09-05"}])
        self.assertNotIn("dimensionFilter", calls[0][2])
        self.assertEqual(calls[1][2]["dimensionFilter"]["filter"]["fieldName"], "eventName")
        saved = (self.workspace / "runtime/analytics/example-project.json").read_text()
        self.assertNotIn("123456789", saved)
        self.assertNotIn("test-access-token-123", saved)
        self.assertEqual((self.workspace / "runtime/analytics/example-project.json").stat().st_mode & 0o777, 0o600)

    def test_disabled_or_unverified_filter_stops_before_network(self) -> None:
        for field, value, message in [
            ("enabled", False, "not enabled"),
            ("internal_test_traffic_excluded", False, "internal-traffic filter"),
        ]:
            with self.subTest(field=field):
                self.config[field] = value
                self.write_config()
                transport = mock.Mock()
                with self.assertRaisesRegex(ga4_connector.GA4ConnectorError, message):
                    ga4_connector.fetch_report(
                        self.workspace,
                        environment={"GROWTH_GA4_ACCESS_TOKEN": "test-access-token-123"},
                        transport=transport,
                    )
                transport.assert_not_called()
                self.config[field] = True

    def test_missing_token_or_incompatible_response_never_replaces_report(self) -> None:
        report = self.workspace / "runtime/analytics/example-project.json"
        report.parent.mkdir()
        report.write_text("previous", encoding="utf-8")
        with self.assertRaisesRegex(ga4_connector.GA4ConnectorError, "ACCESS_TOKEN"):
            ga4_connector.fetch_report(self.workspace, environment={})
        with self.assertRaisesRegex(ga4_connector.GA4ConnectorError, "incompatible"):
            ga4_connector.fetch_report(
                self.workspace,
                environment={"GROWTH_GA4_ACCESS_TOKEN": "test-access-token-123"},
                transport=lambda *_args: {"rows": []},
            )
        self.assertEqual(report.read_text(), "previous")

    def test_config_and_report_symlinks_fail_closed(self) -> None:
        config = self.workspace / "runtime/ga4.json"
        real = self.workspace / "real.json"
        real.write_text(config.read_text(), encoding="utf-8")
        config.unlink()
        config.symlink_to(real)
        with self.assertRaisesRegex(ga4_connector.GA4ConnectorError, "configuration"):
            ga4_connector.fetch_report(self.workspace, environment={})
        config.unlink()
        self.write_config()
        analytics = self.workspace / "runtime/analytics"
        analytics.mkdir()
        target = analytics / "example-project.json"
        target.symlink_to(real)
        responses = iter((self.response(1, 1), self.response(0)))
        with self.assertRaisesRegex(ga4_connector.GA4ConnectorError, "stored safely"):
            ga4_connector.fetch_report(
                self.workspace,
                environment={"GROWTH_GA4_ACCESS_TOKEN": "test-access-token-123"},
                transport=lambda *_args: next(responses),
            )

    def test_invalid_property_event_and_open_permissions_fail_closed(self) -> None:
        for field, value in [("property_id", "properties/123"), ("download_event_name", "bad event")]:
            with self.subTest(field=field):
                original = self.config[field]
                self.config[field] = value
                self.write_config()
                with self.assertRaises(ga4_connector.GA4ConnectorError):
                    ga4_connector.fetch_report(self.workspace, environment={})
                self.config[field] = original
        self.write_config()
        (self.workspace / "runtime/ga4.json").chmod(0o644)
        with self.assertRaisesRegex(ga4_connector.GA4ConnectorError, "unsafe"):
            ga4_connector.fetch_report(self.workspace, environment={})

    def test_readiness_is_safe_and_project_scoped(self) -> None:
        self.assertEqual(
            ga4_connector.readiness(self.workspace, "other", environment={}),
            {"state": "configuration_required"},
        )
        self.config["enabled"] = False
        self.write_config()
        self.assertEqual(
            ga4_connector.readiness(self.workspace, "example-project", environment={}),
            {"state": "disabled"},
        )
        self.config["enabled"] = True
        self.write_config()
        self.assertEqual(
            ga4_connector.readiness(self.workspace, "example-project", environment={}),
            {"state": "authentication_required"},
        )
        self.assertEqual(
            ga4_connector.readiness(
                self.workspace, "example-project",
                environment={"GROWTH_GA4_ACCESS_TOKEN": "test-access-token-123"},
            ),
            {"state": "ready"},
        )


if __name__ == "__main__":
    unittest.main()
