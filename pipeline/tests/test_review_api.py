from __future__ import annotations

import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from pipeline import root_writer
from pipeline.review_api import (
    ReviewError,
    ReviewService,
    create_server,
    validate_pending_manifest_boundary,
)


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class ReviewAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "workspace"
        self.workspace.mkdir()
        (self.workspace / "AGENTS.md").write_text("# Test\n", encoding="utf-8")
        self._create_project("alpha-profile", "alpha", "Alpha")
        self._create_project("beta-profile", "beta", "Beta")
        self.alpha = self._create_review(
            "alpha-profile",
            "alpha",
            artifact_id="launch-brief",
            revision="r1",
            title="Launch brief",
            content=b"alpha launch artifact\n",
        )
        self.beta = self._create_review(
            "beta-profile",
            "beta",
            artifact_id="launch-brief",
            revision="r1",
            title="Beta launch brief",
            content=b"beta launch artifact\n",
        )
        self.service = ReviewService(self.workspace)

    def tearDown(self) -> None:
        self.service.close()
        self.temp.cleanup()

    def _create_project(self, package: str, project_id: str, display_name: str) -> None:
        root = self.workspace / "projects" / package
        root.mkdir(parents=True)
        source_schemas = Path(__file__).resolve().parents[2] / "contracts" / "schemas"
        schema_root = root / "schemas"
        schema_root.mkdir()
        schema_entries: dict[str, dict[str, str]] = {}
        for schema_id, filename in (
            ("project-start-brief@1", "project-start-brief.schema.json"),
            ("project-start-brief@2", "project-start-brief-v2.schema.json"),
            ("project-start-readiness@1", "project-start-readiness.schema.json"),
        ):
            content = (source_schemas / filename).read_bytes()
            (schema_root / filename).write_bytes(content)
            schema_entries[schema_id] = {
                "path": f"schemas/{filename}",
                "sha256": digest(content),
            }
        (root / "schema-registry.json").write_text(
            json.dumps({"registry_version": "1.0", "schemas": schema_entries}),
            encoding="utf-8",
        )
        profile = {
            "profile_version": "1.0",
            "profile_revision": f"{project_id}-profile-v1",
            "project_id": project_id,
            "display_name": display_name,
            "root_role_id": f"{project_id}-root",
            "schema_registry": "schema-registry.json",
            "writer": {
                "state_root": f"projects/{package}/state",
                "allowed_roots": ["clockwork/run-receipts", "outbox", "records"],
                "append_only_roots": [
                    "clockwork/run-receipts",
                    "outbox",
                    "records",
                ],
                "receipt_root": "clockwork/run-receipts",
                "max_files_per_request": 8,
                "max_total_bytes": 100_000,
            },
        }
        (root / "project.json").write_text(
            json.dumps(profile), encoding="utf-8"
        )

    def _create_review(
        self,
        package: str,
        project_id: str,
        *,
        artifact_id: str,
        revision: str,
        title: str,
        content: bytes,
    ) -> dict[str, object]:
        state = self.workspace / "projects" / package / "state"
        artifact_relative = f"outbox/artifacts/{artifact_id}/{revision}.md"
        artifact_path = state / artifact_relative
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(content)
        evidence_relative = f"evidence/{artifact_id}/{revision}.txt"
        evidence_content = f"Evidence for {project_id}: {title}\n".encode("utf-8")
        evidence_path = state / evidence_relative
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_bytes(evidence_content)
        manifest = {
            "review_item_version": "1.0",
            "project_id": project_id,
            "project_profile_revision": f"{project_id}-profile-v1",
            "artifact_id": artifact_id,
            "artifact_revision": revision,
            "artifact_path": artifact_relative,
            "artifact_sha256": digest(content),
            "title": title,
            "type": "content-package",
            "preview": f"Preview for {title}",
            "evidence": [{"label": "Source packet", "ref": evidence_relative}],
            "quality_checks": [
                {"name": "claim check", "status": "pass", "detail": "3/3 claims cited"}
            ],
        }
        manifest_path = state / "outbox/pending" / artifact_id / f"{revision}.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return {
            "manifest": manifest,
            "manifest_path": manifest_path,
            "artifact_path": artifact_path,
            "content": content,
            "evidence_path": evidence_path,
            "evidence_ref": evidence_relative,
            "evidence_content": evidence_content,
        }

    def _action(
        self,
        action: str,
        review: dict[str, object] | None = None,
        **extra: object,
    ) -> dict[str, object]:
        selected = review or self.alpha
        manifest = selected["manifest"]
        assert isinstance(manifest, dict)
        value: dict[str, object] = {
            "action": action,
            "project_id": manifest["project_id"],
            "project_profile_revision": manifest["project_profile_revision"],
            "artifact_id": manifest["artifact_id"],
            "artifact_revision": manifest["artifact_revision"],
            "artifact_sha256": manifest["artifact_sha256"],
        }
        value.update(extra)
        return value

    def _action_record_path(self, package: str = "alpha-profile") -> Path:
        return (
            self.workspace
            / "projects"
            / package
            / "state/outbox/review-actions/launch-brief/r1.json"
        )

    def _project_brief(self, project_id: str = "alpha") -> dict[str, object]:
        return {
            "project_id": project_id,
            "project_profile_revision": f"{project_id}-profile-v1",
            "goal": {
                "title": "Clarify activation",
                "detail": "Identify the clearest first-use outcome for this project.",
            },
            "audience": {
                "label": "New operators",
                "detail": "People evaluating a new workflow before committing time to it.",
            },
            "success_signal": {
                "label": "Useful direction",
                "detail": "A source-backed next step that the project owner can review.",
            },
            "baseline": {
                "status": "measured",
                "detail": "The current baseline is a bounded synthetic test state, not live-market performance.",
            },
            "horizon": {
                "label": "One research cycle",
                "detail": "Produce one reviewable recommendation during the next bounded research cycle.",
            },
            "resources": {
                "detail": "Use the existing project facts, approved evidence boundary, and local control plane only.",
            },
            "do_nothing_option": {
                "detail": "Keep the project unchanged until credible evidence supports a better next step.",
            },
            "non_goals": [
                "No public launch or account changes.",
                "No unsupported product claims.",
            ],
            "research_question": "Which first-use problem deserves evidence-backed investigation first?",
        }

    def test_project_discovery_and_switching_are_isolated(self) -> None:
        self.assertEqual(
            self.service.projects(),
            [
                {
                    "project_id": "alpha",
                    "display_name": "Alpha",
                    "project_profile_revision": "alpha-profile-v1",
                },
                {
                    "project_id": "beta",
                    "display_name": "Beta",
                    "project_profile_revision": "beta-profile-v1",
                },
            ],
        )
        alpha = self.service.reviews("alpha")
        beta = self.service.reviews("beta")
        self.assertEqual(alpha["reviews"][0]["review_item_version"], "1.0")
        self.assertEqual([item["title"] for item in alpha["reviews"]], ["Launch brief"])
        self.assertEqual([item["title"] for item in beta["reviews"]], ["Beta launch brief"])
        self.assertNotEqual(
            alpha["reviews"][0]["artifact_sha256"],
            beta["reviews"][0]["artifact_sha256"],
        )

    def test_projects_accept_current_profile_metadata_extensions(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        projects = {
            item["project_id"]: item
            for item in ReviewService(repository).projects()
        }
        profiles = sorted((repository / "projects").glob("*/project.json"))
        expected: dict[str, dict[str, str]] = {}
        extended_profiles = 0
        for profile_path in profiles:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            expected[profile["project_id"]] = {
                "project_id": profile["project_id"],
                "display_name": profile["display_name"],
                "project_profile_revision": profile["profile_revision"],
            }
            if {
                "professional_contracts",
                "research_adapters",
            }.issubset(profile):
                extended_profiles += 1

        self.assertGreater(extended_profiles, 0)
        self.assertEqual(projects, expected)

    def test_projects_rejects_an_unrelated_profile_field(self) -> None:
        profile_path = self.workspace / "projects/alpha-profile/project.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["unrelated_metadata"] = {"must_not_be_accepted": True}
        profile_path.write_text(json.dumps(profile), encoding="utf-8")

        with self.assertRaisesRegex(ReviewError, "unknown fields: unrelated_metadata"):
            self.service.projects()

    def test_review_returns_exact_verified_utf8_artifact_not_manifest_preview(self) -> None:
        result = self.service.reviews("alpha")["reviews"][0]
        expected = self.alpha["content"].decode("utf-8")
        self.assertEqual(result["artifact_content"], expected)
        self.assertNotEqual(result["artifact_content"], result["preview"])
        self.assertEqual(
            digest(result["artifact_content"].encode("utf-8")),
            result["artifact_sha256"],
        )

    def test_review_pins_and_viewer_returns_exact_verified_utf8_evidence(self) -> None:
        review = self.service.reviews("alpha")["reviews"][0]
        reference = review["evidence"][0]
        expected = self.alpha["evidence_content"]
        self.assertEqual(reference["ref"], self.alpha["evidence_ref"])
        self.assertEqual(reference["sha256"], digest(expected))

        result = self.service.evidence(
            "alpha", reference["ref"], reference["sha256"]
        )
        self.assertEqual(result["project_id"], "alpha")
        self.assertEqual(result["project_profile_revision"], "alpha-profile-v1")
        self.assertEqual(result["evidence_ref"], reference["ref"])
        self.assertEqual(result["evidence_sha256"], digest(expected))
        self.assertEqual(result["byte_length"], len(expected))
        self.assertEqual(result["content"].encode("utf-8"), expected)

    def test_review_can_show_its_own_governance_report(self) -> None:
        state = self.workspace / "projects/alpha-profile/state"
        relative = "records/governance/launch-brief-final-review-r1.md"
        content = b"# Final governance review\n\nExact internal review evidence.\n"
        path = state / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        manifest = self.alpha["manifest"]
        assert isinstance(manifest, dict)
        manifest["evidence"].append({"label": "Final governance review", "ref": relative})
        self.alpha["manifest_path"].write_text(json.dumps(manifest), encoding="utf-8")

        review = self.service.reviews("alpha")["reviews"][0]
        reference = review["evidence"][1]
        result = self.service.evidence("alpha", reference["ref"], reference["sha256"])

        self.assertEqual(reference["ref"], relative)
        self.assertEqual(result["content"].encode("utf-8"), content)

    def test_evidence_viewer_rejects_changed_and_non_utf8_bytes(self) -> None:
        review = self.service.reviews("alpha")["reviews"][0]
        reference = review["evidence"][0]
        self.alpha["evidence_path"].write_bytes(b"changed after queue load\n")
        with self.assertRaises(ReviewError) as changed:
            self.service.evidence("alpha", reference["ref"], reference["sha256"])
        self.assertEqual(changed.exception.status, 409)
        self.assertEqual(changed.exception.code, "evidence_hash_mismatch")

        binary = b"\xff\xfe\x00binary"
        self.alpha["evidence_path"].write_bytes(binary)
        with self.assertRaises(ReviewError) as unsupported:
            self.service.evidence("alpha", reference["ref"], digest(binary))
        self.assertEqual(unsupported.exception.status, 422)
        self.assertEqual(unsupported.exception.code, "render_adapter_required")

    def test_evidence_viewer_confines_paths_to_selected_project_state(self) -> None:
        outside = Path(self.temp.name) / "outside-evidence.txt"
        outside.write_text("outside remains private", encoding="utf-8")
        beta_bytes = self.beta["evidence_content"]
        attempts = [
            "../beta-profile/state/evidence/launch-brief/r1.txt",
            "evidence/../../beta-profile/state/evidence/launch-brief/r1.txt",
            str(outside),
            "outbox/artifacts/launch-brief/r1.md",
        ]
        for attempt in attempts:
            with self.subTest(ref=attempt), self.assertRaises(ReviewError):
                self.service.evidence("alpha", attempt, digest(beta_bytes))
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside remains private")

        symlink = (
            self.workspace
            / "projects/alpha-profile/state/evidence/cross-project.txt"
        )
        symlink.symlink_to(self.beta["evidence_path"])
        with self.assertRaises(ReviewError) as linked:
            self.service.evidence("alpha", "evidence/cross-project.txt", digest(beta_bytes))
        self.assertEqual(linked.exception.status, 409)
        self.assertEqual(linked.exception.code, "invalid_state")

    def test_non_utf8_artifact_fails_closed_until_render_adapter_exists(self) -> None:
        self._create_review(
            "alpha-profile",
            "alpha",
            artifact_id="binary-creative",
            revision="r1",
            title="Binary creative",
            content=b"\xff\xd8\xff\x00binary",
        )
        with self.assertRaises(ReviewError) as caught:
            self.service.reviews("alpha")
        self.assertEqual(caught.exception.status, 422)
        self.assertEqual(caught.exception.code, "render_adapter_required")
        self.assertIn("exact-byte render adapter", caught.exception.message)
        self.assertFalse(
            (
                self.workspace
                / "projects/alpha-profile/state/outbox/review-actions/binary-creative/r1.json"
            ).exists()
        )

    def test_checked_in_contract_fixtures_match_service_boundary(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        profile = root_writer.load_project_profile(
            repository / "projects/example/project.json"
        )
        fixture_root = repository / "projects/example/fixtures/schema-contracts"

        valid_item = json.loads(
            (fixture_root / "outbox-review/01-valid.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            validate_pending_manifest_boundary(valid_item, profile), valid_item
        )
        invalid_item = dict(valid_item)
        invalid_item["artifact_path"] = "content/demo-brief/r1.md"
        with self.assertRaises(ReviewError):
            validate_pending_manifest_boundary(invalid_item, profile)

        note = json.loads(
            (fixture_root / "human-review/01-note-valid.json").read_text(
                encoding="utf-8"
            )
        )
        approve = {
            **note,
            "action_id": "11111111-1111-4111-8111-111111111111",
            "action": "approve",
            "note": None,
            "reason": None,
            "rework_requested": False,
        }
        decline = {
            **note,
            "action_id": "22222222-2222-4222-8222-222222222222",
            "action": "decline",
            "note": None,
            "reason": "The synthetic evidence does not support the decision.",
            "rework_requested": False,
        }
        for record in (approve, decline, note):
            self.assertEqual(
                self.service._validated_action_record(record, profile), record
            )

        invalid_approve = {**approve, "artifact_sha256": "latest"}
        invalid_decline = {**decline, "reason": None}
        invalid_note = {**note, "rework_requested": False}
        for record in (invalid_approve, invalid_decline, invalid_note):
            with self.assertRaises(ReviewError):
                self.service._validated_action_record(record, profile)

    def test_approve_is_immutable_and_updates_derived_status(self) -> None:
        before = self.alpha["artifact_path"].read_bytes()
        record, replay = self.service.review_action(self._action("approve"))
        self.assertFalse(replay)
        self.assertEqual(record["action"], "approve")
        self.assertEqual(record["actor_type"], "human")
        self.assertRegex(record["recorded_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertFalse(record["rework_requested"])
        self.assertEqual(self.alpha["artifact_path"].read_bytes(), before)
        stored = json.loads(self._action_record_path().read_text(encoding="utf-8"))
        self.assertEqual(stored, record)
        self.assertEqual(self.service.reviews("alpha")["reviews"][0]["status"], "approved")

    def test_decline_requires_reason_and_derives_declined_status(self) -> None:
        with self.assertRaisesRegex(ReviewError, "missing fields: reason"):
            self.service.review_action(self._action("decline"))
        record, replay = self.service.review_action(
            self._action("decline", reason="Evidence does not support the headline.")
        )
        self.assertFalse(replay)
        self.assertEqual(record["reason"], "Evidence does not support the headline.")
        self.assertEqual(self.service.reviews("alpha")["reviews"][0]["status"], "declined")

    def test_note_records_rework_signal_without_recreating_product(self) -> None:
        pending_before = sorted(
            str(path.relative_to(self.workspace))
            for path in self.workspace.rglob("*.md")
        )
        record, replay = self.service.review_action(
            self._action("note", note="Make the value proof visible above the fold.")
        )
        self.assertFalse(replay)
        self.assertTrue(record["rework_requested"])
        self.assertEqual(record["note"], "Make the value proof visible above the fold.")
        self.assertEqual(self.service.reviews("alpha")["reviews"][0]["status"], "rework_requested")
        self.assertEqual(
            sorted(str(path.relative_to(self.workspace)) for path in self.workspace.rglob("*.md")),
            pending_before,
        )

    def test_exact_revision_profile_and_actual_hash_are_required(self) -> None:
        wrong_hash = self._action("approve")
        wrong_hash["artifact_sha256"] = "0" * 64
        with self.assertRaisesRegex(ReviewError, "actual artifact bytes"):
            self.service.review_action(wrong_hash)

        stale = self._action("approve")
        stale["project_profile_revision"] = "alpha-profile-v0"
        with self.assertRaisesRegex(ReviewError, "stale project profile"):
            self.service.review_action(stale)

        missing_revision = self._action("approve")
        missing_revision["artifact_revision"] = "r2"
        with self.assertRaisesRegex(ReviewError, "Exact review revision"):
            self.service.review_action(missing_revision)

        self.alpha["artifact_path"].write_bytes(b"tampered after manifest\n")
        with self.assertRaisesRegex(ReviewError, "do not match"):
            self.service.review_action(self._action("approve"))
        self.assertFalse(self._action_record_path().exists())

    def test_cross_project_action_is_rejected(self) -> None:
        cross = self._action("approve", self.beta)
        cross["project_id"] = "alpha"
        cross["project_profile_revision"] = "alpha-profile-v1"
        with self.assertRaises(ReviewError) as caught:
            self.service.review_action(cross)
        self.assertIn(caught.exception.code, {"artifact_hash_mismatch", "review_not_found"})
        self.assertFalse(self._action_record_path().exists())
        self.assertFalse(self._action_record_path("beta-profile").exists())

    def test_identical_retry_is_idempotent_and_conflict_is_terminal(self) -> None:
        request = self._action("note", note="Shorten the opening.")
        first, first_replay = self.service.review_action(request)
        proposed_later = self.service._canonical_action_record(
            self.service._validate_action_input(request),
            recorded_at="2099-01-01T00:00:00Z",
        )
        with patch.object(
            self.service, "_canonical_action_record", return_value=proposed_later
        ):
            second, second_replay = self.service.review_action(request)
        self.assertFalse(first_replay)
        self.assertTrue(second_replay)
        self.assertEqual(first, second)
        self.assertEqual(first["recorded_at"], second["recorded_at"])

        with self.assertRaisesRegex(ReviewError, "terminal action"):
            self.service.review_action(
                self._action("decline", reason="Use another direction.")
            )
        records = list(
            (self.workspace / "projects/alpha-profile/state/outbox/review-actions").rglob("*.json")
        )
        self.assertEqual(len(records), 1)

    def test_corrupt_action_identity_cannot_drive_review_status(self) -> None:
        record, _ = self.service.review_action(self._action("approve"))
        record["artifact_sha256"] = "0" * 64
        self._action_record_path().write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(ReviewError, "action_id does not match|does not bind"):
            self.service.reviews("alpha")

    def test_action_without_root_writer_receipt_is_not_terminal(self) -> None:
        action = self._action("approve")
        record = self.service._canonical_action_record(
            self.service._validate_action_input(action)
        )
        path = self._action_record_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ReviewError, "completed Root Writer receipt"):
            self.service.reviews("alpha")

    def test_unsafe_artifact_path_cannot_write_or_read_outside_project(self) -> None:
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_text("untouched", encoding="utf-8")
        manifest = self.alpha["manifest"]
        assert isinstance(manifest, dict)
        manifest["artifact_path"] = "../../../../outside.txt"
        self.alpha["manifest_path"].write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ReviewError):
            self.service.review_action(self._action("approve"))
        self.assertEqual(outside.read_text(encoding="utf-8"), "untouched")
        self.assertFalse(self._action_record_path().exists())

    def test_unsafe_manifest_evidence_ref_is_rejected_before_queue_delivery(self) -> None:
        manifest = self.alpha["manifest"]
        assert isinstance(manifest, dict)
        manifest["evidence"] = [
            {"label": "Cross-project", "ref": "../beta-profile/state/evidence/source.txt"}
        ]
        self.alpha["manifest_path"].write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ReviewError):
            self.service.reviews("alpha")

    def test_http_endpoints_and_local_only_bind(self) -> None:
        with self.assertRaisesRegex(ReviewError, "127.0.0.1"):
            create_server(self.workspace, host="0.0.0.0", port=0)

        server = create_server(self.workspace, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request("GET", "/api/projects")
            response = connection.getresponse()
            projects = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(len(projects["projects"]), 2)

            connection.request("GET", "/api/setup-status")
            response = connection.getresponse()
            setup = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(setup["desk"], "running")
            self.assertEqual(setup["autostart"]["state"], "build_required")
            self.assertEqual(setup["research_schedule"]["state"], "configuration_required")
            self.assertEqual(setup["remote_access"]["state"], "local_only")
            self.assertEqual(setup["publication"], {
                "mode": "not_configured", "publisher": "disabled",
            })

            connection.request("GET", "/api/website-analytics?project_id=alpha")
            response = connection.getresponse()
            analytics = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(analytics, {
                "project_id": "alpha", "status": "not_connected", "source": "ga4",
                "report": None, "setup": {"state": "configuration_required"},
            })
            connection.request("GET", "/api/platform-analytics?project_id=alpha")
            response = connection.getresponse()
            platform_analytics = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(platform_analytics["status"], "not_connected")
            self.assertEqual(platform_analytics["source"], "native_platform_analytics")

            body = json.dumps(self._action("approve"))
            connection.request(
                "POST",
                "/api/review-actions",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            action = json.loads(response.read())
            self.assertEqual(response.status, 201)
            self.assertEqual(action["action_record"]["action"], "approve")
            self.assertFalse(action["idempotent_replay"])
            self.assertEqual(action["automation"]["status"], "needs_attention")

            connection.request("GET", "/api/reviews?project_id=alpha")
            response = connection.getresponse()
            reviews = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(reviews["reviews"][0]["status"], "approved")

            evidence = reviews["reviews"][0]["evidence"][0]
            query = urlencode(
                {
                    "project_id": "alpha",
                    "ref": evidence["ref"],
                    "sha256": evidence["sha256"],
                }
            )
            connection.request("GET", f"/api/evidence?{query}")
            response = connection.getresponse()
            viewed = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(
                viewed["content"].encode("utf-8"), self.alpha["evidence_content"]
            )
            self.assertEqual(viewed["evidence_sha256"], evidence["sha256"])
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_built_desk_serves_only_confined_static_assets_with_security_headers(self) -> None:
        static = self.workspace / "dashboard/dist"
        (static / "assets").mkdir(parents=True)
        (static / "index.html").write_text("<!doctype html><title>Built Desk</title>", encoding="utf-8")
        (static / "assets/app.js").write_text("console.log('built')", encoding="utf-8")
        outside = self.workspace / "AGENTS.md"
        (static / "assets/link.js").symlink_to(outside)
        server = create_server(self.workspace, port=0, static_root=static)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("GET", "/?project=alpha&view=insights")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn(b"Built Desk", response.read())
            self.assertEqual(response.getheader("X-Frame-Options"), "DENY")
            self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"))
            self.assertEqual(response.getheader("Cache-Control"), "no-cache")

            connection.request("HEAD", "/")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"")
            self.assertEqual(response.getheader("Content-Length"), str(len("<!doctype html><title>Built Desk</title>")))

            connection.request("GET", "/assets/app.js")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"console.log('built')")
            self.assertIn("immutable", response.getheader("Cache-Control"))

            connection.request("GET", "/", headers={"Host": "rebinding.example"})
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 403)

            for unsafe in ("/..%2FAGENTS.md", "/assets/link.js", "/missing.js"):
                connection.request("GET", unsafe)
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 404)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_built_desk_rejects_any_static_root_outside_dashboard_dist(self) -> None:
        other = self.workspace / "other"
        other.mkdir()
        (other / "index.html").write_text("wrong", encoding="utf-8")
        with self.assertRaisesRegex(ReviewError, "dashboard/dist"):
            create_server(self.workspace, port=0, static_root=other)

    def test_project_desk_http_creates_an_immutable_brief_without_publication(self) -> None:
        """The start surface persists one operator brief, not a model run or publish action."""

        outbox = self.workspace / "projects/alpha-profile/state/outbox"
        before_outbox = {
            path.relative_to(outbox).as_posix(): path.read_bytes()
            for path in outbox.rglob("*")
            if path.is_file()
        }
        server = create_server(self.workspace, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request("GET", "/api/project-desk?project_id=alpha")
            response = connection.getresponse()
            empty_desk = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(empty_desk["phase"], "brief_required")
            self.assertEqual(empty_desk["readiness"]["completed"], 0)
            self.assertEqual(empty_desk["automation"]["action"], "unconfigured")
            self.assertEqual(empty_desk["automation"]["active_roles"], [])
            self.assertNotIn("artifact_ref", json.dumps(empty_desk))
            self.assertNotIn("sha256", json.dumps(empty_desk).casefold())

            payload = self._project_brief()
            connection.request(
                "POST",
                "/api/project-briefs",
                body=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            created = json.loads(response.read())
            self.assertEqual(response.status, 201)
            self.assertTrue(created["created"])
            self.assertEqual(created["brief"]["project_id"], "alpha")
            self.assertEqual(created["brief"]["revision"], "r1")
            self.assertNotIn("writer_receipt", json.dumps(created))
            self.assertFalse(created["desk"]["authority"]["automatic_publish"])
            self.assertTrue(created["desk"]["authority"]["human_review_required"])
            self.assertTrue(created["desk"]["authority"]["ready_to_queue_is_not_go_live"])
            customer_response = json.dumps(created).casefold()
            for internal in (
                "artifact_ref",
                "sha256",
                "writer_receipt",
                "records/project-start",
                "clockwork/",
                "credential",
            ):
                self.assertNotIn(internal, customer_response)

            connection.request("GET", "/api/project-desk?project_id=alpha")
            response = connection.getresponse()
            desk = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(desk["goal"], payload["goal"])
            self.assertEqual(desk["audience"], payload["audience"])
            self.assertEqual(desk["success_signal"], payload["success_signal"])
            self.assertEqual(desk["baseline"], payload["baseline"])
            self.assertEqual(desk["horizon"], payload["horizon"])
            self.assertEqual(desk["resources"], payload["resources"])
            self.assertEqual(desk["do_nothing_option"], payload["do_nothing_option"])
            self.assertEqual(desk["non_goals"], payload["non_goals"])
            self.assertEqual(desk["phase"], "context_blocked")

            connection.request(
                "POST",
                "/api/project-briefs",
                body=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            replay = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertFalse(replay["created"])
            self.assertEqual(replay["brief"], created["brief"])
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        stored_briefs = list(
            (
                self.workspace
                / "projects/alpha-profile/state/records/project-start/briefs"
            ).rglob("*.json")
        )
        self.assertEqual(len(stored_briefs), 1)
        after_outbox = {
            path.relative_to(outbox).as_posix(): path.read_bytes()
            for path in outbox.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after_outbox, before_outbox)

    def test_goal_loop_http_records_a_local_check_without_research_or_publication(self) -> None:
        """The first goal loop is durable, backgrounded, and explicitly local-only."""

        outbox = self.workspace / "projects/alpha-profile/state/outbox"
        before_outbox = {
            path.relative_to(outbox).as_posix(): path.read_bytes()
            for path in outbox.rglob("*")
            if path.is_file()
        }
        server = create_server(self.workspace, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request("GET", "/api/goal-loop?project_id=alpha")
            response = connection.getresponse()
            before = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(before["job"], {
                "id": None,
                "status": "not_started",
                "request_recorded": False,
                "result": None,
            })

            request = {
                "project_id": "alpha",
                "project_profile_revision": "alpha-profile-v1",
            }
            connection.request(
                "POST",
                "/api/goal-loop",
                body=json.dumps(request),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            requires_brief = json.loads(response.read())
            self.assertEqual(response.status, 409)
            self.assertEqual(requires_brief["error"]["code"], "brief_required")

            connection.request(
                "POST",
                "/api/project-briefs",
                body=json.dumps(self._project_brief()),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 201)
            response.read()

            connection.request(
                "POST",
                "/api/goal-loop",
                body=json.dumps(request),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            queued = json.loads(response.read())
            self.assertEqual(response.status, 202)
            self.assertEqual(queued["job"]["status"], "queued")
            self.assertTrue(queued["job"]["request_recorded"])
            self.assertIsNone(queued["job"]["result"])

            completed: dict[str, object] | None = None
            for _ in range(50):
                connection.request("GET", "/api/goal-loop?project_id=alpha")
                response = connection.getresponse()
                current = json.loads(response.read())
                self.assertEqual(response.status, 200)
                if current["job"]["status"] == "completed":
                    completed = current
                    break
                time.sleep(0.02)
            self.assertIsNotNone(completed)
            assert completed is not None
            result = completed["job"]["result"]
            assert isinstance(result, dict)
            self.assertEqual(result["outcome"], "needs_input")
            self.assertIn("No web research", result["summary"])
            self.assertEqual(
                [item["state"] for item in result["insights"]],
                ["missing", "not_run", "protected"],
            )
            self.assertNotIn("artifact_ref", json.dumps(completed))
            self.assertNotIn("sha256", json.dumps(completed).casefold())
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        records = self.workspace / "projects/alpha-profile/state/records/goal-loops"
        self.assertEqual(len(list(records.rglob("request-r1.json"))), 1)
        self.assertEqual(len(list(records.rglob("result-r1.json"))), 1)
        after_outbox = {
            path.relative_to(outbox).as_posix(): path.read_bytes()
            for path in outbox.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after_outbox, before_outbox)

    def test_project_brief_http_rejects_stale_and_ambiguous_inputs(self) -> None:
        server = create_server(self.workspace, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            stale = self._project_brief()
            stale["project_profile_revision"] = "alpha-profile-v0"
            connection.request(
                "POST",
                "/api/project-briefs",
                body=json.dumps(stale),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            stale_result = json.loads(response.read())
            self.assertEqual(response.status, 409)
            self.assertEqual(stale_result["error"]["code"], "stale_project_profile")
            self.assertEqual(
                stale_result["error"]["message"],
                "This project changed. Refresh the desk before creating a brief.",
            )

            invalid = self._project_brief()
            invalid["unexpected"] = "not accepted"
            connection.request(
                "POST",
                "/api/project-briefs",
                body=json.dumps(invalid),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            invalid_result = json.loads(response.read())
            self.assertEqual(response.status, 400)
            self.assertEqual(invalid_result["error"]["code"], "invalid_project_brief")
            self.assertEqual(
                invalid_result["error"]["message"],
                "Complete the required project brief fields and try again.",
            )

            duplicate = json.dumps(self._project_brief())[:-1]
            duplicate += ',"project_id":"beta"}'
            connection.request(
                "POST",
                "/api/project-briefs",
                body=duplicate,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            duplicate_result = json.loads(response.read())
            self.assertEqual(response.status, 400)
            self.assertEqual(duplicate_result["error"]["code"], "invalid_json")
            self.assertEqual(
                duplicate_result["error"]["message"],
                "Request body must be strict UTF-8 JSON",
            )

            connection.request(
                "POST",
                "/api/project-briefs",
                body="{",
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            malformed_result = json.loads(response.read())
            self.assertEqual(response.status, 400)
            self.assertEqual(malformed_result["error"]["code"], "invalid_json")
            self.assertEqual(
                malformed_result["error"]["message"],
                "Request body must be strict UTF-8 JSON",
            )
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_project_desk_keeps_an_unmeasured_baseline_visible(self) -> None:
        """A declared gap stays visible without blocking qualitative research by itself."""

        server = create_server(self.workspace, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            payload = self._project_brief()
            payload["baseline"] = {
                "status": "not_measured",
                "detail": "No trustworthy baseline is available yet, so the project must not claim measurement readiness.",
            }
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request(
                "POST",
                "/api/project-briefs",
                body=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            created = json.loads(response.read())
            self.assertEqual(response.status, 201)
            desk = created["desk"]
            self.assertEqual(desk["phase"], "context_blocked")
            self.assertEqual(desk["readiness"]["total"], 5)
            self.assertEqual(desk["readiness"]["steps"][0]["id"], "baseline")
            self.assertEqual(desk["readiness"]["steps"][0]["status"], "blocked")
            self.assertIn(
                "Baseline needed",
                [item["title"] for item in desk["blockers"]],
            )
            analytics = next(
                item
                for item in desk["next_roles"]
                if item["role"] == "analytics-experimentation"
            )
            self.assertEqual(analytics["status"], "active")
            self.assertFalse(desk["authority"]["automatic_publish"])
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_checked_in_example_pending_action_works_in_an_isolated_copy(self) -> None:
        """Exercise the real synthetic Example manifest without mutating this checkout.

        The dashboard may be pointed at the checked-in Example pending item during
        local development. This guards the complete HTTP action path against a
        profile or Root Writer integration drift while keeping the source artifact
        and its actual review state untouched.
        """

        repository = Path(__file__).resolve().parents[2]
        source_artifact = (
            repository
            / "projects/example/fixtures/runtime/workhorse-results/research-r1.artifact.json"
        )
        self.assertTrue(source_artifact.is_file())
        source_bytes = source_artifact.read_bytes()

        with tempfile.TemporaryDirectory(prefix="review-api-example-copy-") as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            (workspace / 'AGENTS.md').write_text('# Isolated synthetic test\n')
            shutil.copytree(repository / "projects/example", workspace / "projects/example",
                            ignore=shutil.ignore_patterns('state'))
            profile = json.loads((workspace / 'projects/example/project.json').read_text())
            state = workspace / 'projects/example/state'
            artifact_ref = 'outbox/artifacts/EX-1000-evidence/research-r1.json'
            artifact_path = state / artifact_ref
            artifact_path.parent.mkdir(parents=True)
            artifact_path.write_bytes(source_bytes)
            manifest_path = state / 'outbox/pending/EX-1000-evidence/research-r1.json'
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(json.dumps(dict(
                review_item_version='1.0', project_id=profile['project_id'],
                project_profile_revision=profile['profile_revision'], artifact_id='EX-1000-evidence',
                artifact_revision='research-r1', artifact_path=artifact_ref,
                artifact_sha256=digest(source_bytes), title='Synthetic research fixture',
                type='research', preview='Synthetic review test', evidence=[], quality_checks=[])))

            server = create_server(workspace, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                connection.request("GET", "/api/reviews?project_id=example-project")
                queue_response = connection.getresponse()
                queue = json.loads(queue_response.read())
                self.assertEqual(queue_response.status, 200)
                review = next(
                    item
                    for item in queue["reviews"]
                    if item["artifact_id"] == "EX-1000-evidence"
                    and item["artifact_revision"] == "research-r1"
                )
                self.assertEqual(review["status"], "pending")

                body = {
                    "action": "note",
                    "project_id": review["project_id"],
                    "project_profile_revision": review["project_profile_revision"],
                    "artifact_id": review["artifact_id"],
                    "artifact_revision": review["artifact_revision"],
                    "artifact_sha256": review["artifact_sha256"],
                    "note": "Isolated regression check; not product feedback.",
                }
                connection.request(
                    "POST",
                    "/api/review-actions",
                    body=json.dumps(body),
                    headers={"Content-Type": "application/json"},
                )
                action_response = connection.getresponse()
                action = json.loads(action_response.read())
                self.assertEqual(action_response.status, 201)
                self.assertFalse(action["idempotent_replay"])
                self.assertEqual(action["action_record"]["action"], "note")
                self.assertEqual(
                    action["action_record"]["artifact_sha256"], body["artifact_sha256"]
                )

                connection.request("GET", "/api/reviews?project_id=example-project")
                after_response = connection.getresponse()
                after = json.loads(after_response.read())
                self.assertEqual(after_response.status, 200)
                selected = next(
                    item
                    for item in after["reviews"]
                    if item["artifact_id"] == "EX-1000-evidence"
                    and item["artifact_revision"] == "research-r1"
                )
                self.assertEqual(selected["status"], "rework_requested")
                self.assertTrue(
                    (
                        workspace
                        / "projects/example/state/outbox/review-actions"
                        / "EX-1000-evidence/research-r1.json"
                    ).is_file()
                )
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        self.assertEqual(source_artifact.read_bytes(), source_bytes)


if __name__ == "__main__":
    unittest.main()
