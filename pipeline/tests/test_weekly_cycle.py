from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import uuid

from pipeline import root_writer, studio, weekly_cycle
from pipeline.task_broker import TaskBroker, read_status
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
        self.assertEqual(first["promotion"]["state"], "waiting_for_active_content")
        self.assertEqual(first["suggestion_id"], second["suggestion_id"])
        self.assertEqual(len(calls), 1)
        self.assertIn("untrusted data", calls[0][0].lower())
        view = studio.read(self.workspace, self.profile)
        suggestion = next(item for item in view["suggestions"]
                          if item["id"] == first["suggestion_id"])
        self.assertEqual(suggestion["basis"], "weekly_feed_hypothesis")
        self.assertEqual(suggestion["audience_id"], self.audience_id)
        self.assertEqual(suggestion["source_urls"], ["https://example.org/source"])
        self.assertEqual(view["proposals"], [])
        self.assertEqual(view["materials"], [])

    @patch("pipeline.weekly_cycle.collect")
    def test_idle_project_promotes_with_exact_source_material_but_no_publication(self, collect: Mock) -> None:
        collect.return_value = self.intake
        pending = self.workspace / self.profile["_state_root"] / "outbox/pending"
        for path in pending.rglob("*.json"):
            path.unlink()
        result = weekly_cycle.run(
            self.workspace, "alpha", executable=Path("/usr/bin/true"),
            generate=lambda *_args, **_kwargs: json.dumps({
                "title": "How to preserve uncertainty in a technical summary",
                "channel": "website", "audience_id": self.audience_id,
                "rationale": "A practical method grounded in the selected source.",
                "source_urls": ["https://example.org/source"],
            }))
        self.assertEqual(result["promotion"]["state"], "not_configured")
        view = studio.read(self.workspace, self.profile)
        self.assertEqual(len(view["proposals"]), 1)
        self.assertEqual(len(view["materials"]), 1)
        self.assertEqual(view["proposals"][0]["material_ids"], [view["materials"][0]["id"]])
        self.assertIn("https://example.org/source", view["materials"][0]["extracted_text"])
        self.assertIn("Untrusted discovery material", view["materials"][0]["extracted_text"])

    @patch("pipeline.weekly_cycle.collect")
    def test_idle_configured_project_runs_research_marketing_and_qa(self, collect: Mock) -> None:
        collect.return_value = self.intake
        pending = self.workspace / self.profile["_state_root"] / "outbox/pending"
        for path in pending.rglob("*.json"):
            path.unlink()
        profile_value = json.loads(self.profile_path.read_text())
        profile_value["writer"]["allowed_roots"].append("evidence/packets")
        self.profile_path.write_text(json.dumps(profile_value))
        self.profile = root_writer.load_project_profile(self.profile_path)
        runtime = self.workspace / "runtime"
        (runtime / "broker").mkdir(parents=True)
        permissions = runtime / "permissions.json"
        permissions.write_text(json.dumps({
            "publish": "review", "agents": {
                "inbox": {"browser": "none", "credentials": "none"},
                "research": {"browser": "read-only", "credentials": "none", "autostart": True},
                "marketing": {"browser": "none", "credentials": "none", "autostart": True},
                "mavery-qa": {"browser": "none", "credentials": "none", "autostart": True},
                "publisher": {"enabled": False, "browser": "none", "credentials": "per-channel-connector-only"},
            }, "connectors": {"postiz": {"enabled": False}},
        }))
        database = runtime / "broker/tasks.sqlite"
        broker = TaskBroker(database, permissions)
        historical = broker.admit(
            project="alpha", origin="schedule", origin_key="historical-failure",
            agent="inbox", input_ref="records/old.json", input_sha256="0" * 64,
            actor="test", not_before=0)
        claim = broker.claim("alpha", historical["task_id"], agent="inbox")
        broker.fail(
            "alpha", historical["task_id"], agent="inbox",
            token=claim["lease_token"], version=claim["version"])
        broker.close()
        with patch("pipeline.marketing_worker._prompt", return_value="Draft the lesson"), \
                patch("pipeline.mavery_qa_worker._prompt", return_value="Review the lesson"), \
                patch("pipeline.codex_worker.generate", return_value="# Complete useful lesson\n\nMethod and limits."):
            result = weekly_cycle.run(
                self.workspace, "alpha", executable=Path("/usr/bin/true"),
                generate=lambda *_args, **_kwargs: json.dumps({
                    "title": "How to preserve uncertainty in a technical summary",
                    "channel": "website", "audience_id": self.audience_id,
                    "rationale": "A practical method grounded in the selected source.",
                    "source_urls": ["https://example.org/source"],
                }))
        self.assertEqual(result["promotion"]["state"], "awaiting_review")
        status = read_status(database, "alpha")
        self.assertEqual(status["counts"], {
            "completed": 3, "awaiting_review": 1, "failed": 1})
        reviews = self.fixture.service.reviews("alpha")["reviews"]
        generated = [item for item in reviews if item["type"] == "agent-draft"]
        self.assertEqual(len(generated), 1)
        self.assertIn("Complete useful lesson", generated[0]["artifact_content"])
        self.assertEqual(generated[0]["status"], "pending")

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
