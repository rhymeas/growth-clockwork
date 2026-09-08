from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import time
import unittest

from pipeline.goal_loop import GoalLoopError, GoalLoopService, _job_id, _request_path
from pipeline.project_start import ProjectStartService


SOURCE_WORKSPACE = Path(__file__).resolve().parents[2]
SOURCE_EXAMPLE = SOURCE_WORKSPACE / "projects" / "example"


class GoalLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name) / "workspace"
        self.workspace.mkdir()
        (self.workspace / "AGENTS.md").write_text("# Test\n", encoding="utf-8")
        self.profile_root = self.workspace / "projects" / "example"
        self.profile_root.parent.mkdir()
        shutil.copytree(SOURCE_EXAMPLE, self.profile_root, ignore=shutil.ignore_patterns('state'))
        self.service = GoalLoopService(self.workspace)

    def tearDown(self) -> None:
        self.service.close()
        self.temporary.cleanup()

    @staticmethod
    def _brief() -> dict[str, object]:
        return {
            "project_id": "example-project",
            "project_profile_revision": "example-project-profile-v2",
            "goal": {
                "title": "Clarify one local growth question",
                "detail": "Record one bounded question before any research, model request, or public work starts.",
            },
            "audience": {
                "label": "Independent product operators",
                "detail": "People choosing a focused next step for a small product without treating assumptions as evidence.",
            },
            "success_signal": {
                "label": "A reviewable starting check",
                "detail": "The desk records the goal, context state, limits, and one next question without generating content.",
            },
            "baseline": {
                "status": "not_measured",
                "detail": "No outcome baseline is measured; the local check must not infer an impact result.",
            },
            "horizon": {
                "label": "One local check",
                "detail": "Review the starting inputs before choosing whether external evidence collection is warranted.",
            },
            "resources": {
                "detail": "Use the local project profile and its pinned context only; no accounts, providers, or customer data are available.",
            },
            "do_nothing_option": {
                "detail": "Keep the current approach if the local context does not yet justify external research or a content route.",
            },
            "non_goals": [
                "Publish content or change a public account.",
                "Treat this local check as audience validation.",
            ],
            "research_question": "What additional evidence is needed before this project can select a credible next route?",
        }

    def _start_payload(self) -> dict[str, str]:
        return {
            "project_id": "example-project",
            "project_profile_revision": "example-project-profile-v2",
        }

    def _wait_for_completion(self) -> dict[str, object]:
        for _ in range(100):
            current = self.service.status("example-project")
            if current["job"]["status"] == "completed":
                return current
            time.sleep(0.01)
        self.fail("The local goal check did not complete")

    def test_goal_check_is_not_started_without_an_operator_brief(self) -> None:
        current = self.service.status("example-project")

        self.assertEqual(current["job"]["status"], "not_started")
        with self.assertRaisesRegex(GoalLoopError, "Set a project brief"):
            self.service.start(self._start_payload())

    def test_goal_check_persists_a_local_result_without_external_authority(self) -> None:
        ProjectStartService(self.workspace).create_start_brief(self._brief())

        before = self.service.status("example-project")
        self.assertEqual(before["job"]["status"], "not_started")
        self.assertFalse(before["job"]["request_recorded"])

        queued = self.service.start(self._start_payload())
        completed = self._wait_for_completion()
        result = completed["job"]["result"]

        self.assertEqual(queued["job"]["status"], "queued")
        self.assertEqual(completed["job"]["status"], "completed")
        self.assertEqual(result["outcome"], "needs_input")
        self.assertIn("No web research", result["summary"])
        self.assertEqual(
            [item["state"] for item in result["insights"]],
            ["ready", "not_run", "protected"],
        )
        self.assertEqual(
            result["limits"],
            [
                "This is a local planning check, not audience validation or market research.",
                "It uses no model provider, analytics property, or publishing connection.",
            ],
        )
        job_id = queued["job"]["id"]
        state_root = self.profile_root / "state"
        self.assertTrue((state_root / "records/goal-loops" / job_id / "request-r1.json").is_file())
        self.assertTrue((state_root / "records/goal-loops" / job_id / "result-r1.json").is_file())
        self.assertFalse((state_root / "outbox").exists())

    def test_same_brief_reuses_its_exact_local_result(self) -> None:
        ProjectStartService(self.workspace).create_start_brief(self._brief())

        first = self.service.start(self._start_payload())
        completed = self._wait_for_completion()
        replay = self.service.start(self._start_payload())

        self.assertEqual(first["job"]["id"], completed["job"]["id"])
        self.assertEqual(replay["job"]["id"], completed["job"]["id"])
        self.assertEqual(replay["job"]["status"], "completed")
        records = list((self.profile_root / "state/records/goal-loops" / first["job"]["id"]).glob("*.json"))
        self.assertEqual(sorted(path.name for path in records), ["request-r1.json", "result-r1.json"])

    def test_durable_queued_request_resumes_after_a_service_restart(self) -> None:
        ProjectStartService(self.workspace).create_start_brief(self._brief())
        profile_path, profile = self.service._selected("example-project")
        brief_pointer, _ = self.service._current_brief(profile_path, profile)
        context = self.service._context_summary(profile_path, profile)

        # Build the exact durable request without scheduling it, simulating an
        # API process that stopped after persistence and before execution.
        job_id = _job_id(profile, brief_pointer)
        request = self.service._request_record(profile, job_id, brief_pointer, context)
        self.service._write_record(
            profile_path,
            profile,
            job_id,
            "request",
            _request_path(job_id),
            request,
        )
        self.service.close()
        self.service = GoalLoopService(self.workspace)

        resumed = self._wait_for_completion()
        self.assertEqual(resumed["job"]["id"], job_id)
        self.assertEqual(resumed["job"]["status"], "completed")

    def test_stale_profile_revision_cannot_queue_a_goal_check(self) -> None:
        ProjectStartService(self.workspace).create_start_brief(self._brief())
        stale = {**self._start_payload(), "project_profile_revision": "stale-profile-v1"}

        with self.assertRaisesRegex(GoalLoopError, "Refresh the desk"):
            self.service.start(stale)

        self.assertFalse((self.profile_root / "state/records/goal-loops").exists())


if __name__ == "__main__":
    unittest.main()
