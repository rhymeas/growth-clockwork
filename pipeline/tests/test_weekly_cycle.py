from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import uuid

from pipeline import studio, weekly_cycle
from pipeline.tests import test_review_api


class WeeklyCycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = test_review_api.ReviewAPITests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.workspace = self.fixture.workspace
        self.profile_path, self.profile = self.fixture.service._selected_profile("alpha")
        audience = studio.write(self.workspace, self.profile_path, self.profile, {
            "project_id": "alpha", "project_profile_revision": "alpha-profile-v1",
            "request_id": str(uuid.uuid4()), "kind": "audience",
            "payload": {"label": "Independent makers", "problem": "Need reliable technical summaries"},
        })
        self.audience_id = audience["studio"]["audiences"][0]["id"]
        self.intake = {
            "reused": False,
            "record": "records/research-feeds/FEED-example/result-r1.json",
            "result": {
                "item_count": 1,
                "sources": [{"items": [{
                    "title": "A useful source", "url": "https://example.org/source",
                    "publisher": "Example", "published": "2026-09-07",
                    "excerpt": "A bounded source excerpt",
                }]}],
            },
        }

    @patch("pipeline.weekly_cycle.collect")
    def test_creates_one_idempotent_source_bound_suggestion(self, collect: Mock) -> None:
        collect.return_value = self.intake
        calls = []

        def generate(prompt, **kwargs):
            calls.append((prompt, kwargs))
            return json.dumps({
                "title": "How to preserve uncertainty in a technical summary",
                "channel": "website", "audience_id": self.audience_id,
                "rationale": "A practical method grounded in the selected source.",
                "source_urls": ["https://example.org/source"],
            })

        first = weekly_cycle.run(
            self.workspace, "alpha", executable=Path("/usr/bin/true"),
            now=datetime(2026, 9, 7, tzinfo=timezone.utc), generate=generate)
        second = weekly_cycle.run(
            self.workspace, "alpha", executable=Path("/usr/bin/true"),
            now=datetime(2026, 9, 7, tzinfo=timezone.utc),
            generate=lambda *_args, **_kwargs: self.fail("idempotent cycle called Codex again"))
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(first["suggestion_id"], second["suggestion_id"])
        self.assertEqual(len(calls), 1)
        self.assertIn("untrusted data", calls[0][0].lower())
        view = studio.read(self.workspace, self.profile)
        suggestion = next(item for item in view["suggestions"]
                          if item["id"] == first["suggestion_id"])
        self.assertEqual(suggestion["basis"], "weekly_feed_hypothesis")
        self.assertEqual(suggestion["audience_id"], self.audience_id)
        self.assertEqual(suggestion["source_urls"], ["https://example.org/source"])

    @patch("pipeline.weekly_cycle.collect")
    def test_rejects_model_urls_and_channels_not_in_the_receipt(self, collect: Mock) -> None:
        collect.return_value = self.intake
        for changed in ({"channel": "x"}, {"source_urls": ["https://other.example/"]}):
            value = {
                "title": "Topic", "channel": "website", "audience_id": None,
                "rationale": "Reason", "source_urls": ["https://example.org/source"],
            } | changed
            with self.subTest(changed=changed), self.assertRaises(weekly_cycle.WeeklyCycleError):
                weekly_cycle.run(
                    self.workspace, "alpha", executable=Path("/usr/bin/true"),
                    generate=lambda *_args, value=value, **_kwargs: json.dumps(value))

    @patch("pipeline.weekly_cycle.collect")
    def test_missing_items_stop_before_codex_or_studio_write(self, collect: Mock) -> None:
        collect.return_value = {
            **self.intake, "result": {"item_count": 0, "sources": [{"items": []}]}}
        generate = Mock()
        with self.assertRaisesRegex(weekly_cycle.WeeklyCycleError, "no usable items"):
            weekly_cycle.run(
                self.workspace, "alpha", executable=Path("/usr/bin/true"), generate=generate)
        generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
