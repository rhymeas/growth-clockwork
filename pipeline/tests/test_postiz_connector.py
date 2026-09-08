from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from pipeline import postiz_connector


class PostizConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name) / "workspace"
        (self.workspace / "runtime").mkdir(parents=True)
        self.profile_path = self.workspace / "projects/example/project.json"
        self.profile_path.parent.mkdir(parents=True)
        self.profile = {
            "project_id": "example-project",
            "profile_revision": "example-v1",
            "root_role_id": "growth-clockwork-root",
        }
        self.package = {
            "release_id": "release-1",
            "artifact_sha256": hashlib.sha256(b"Useful approved text").hexdigest(),
        }
        self.verified = {
            "workspace": self.workspace,
            "profile_path": self.profile_path,
            "profile": self.profile,
            "pointer": {"artifact_ref": "records/release-packages/a/r.json", "artifact_revision": "release-1", "artifact_sha256": "a" * 64},
            "package": self.package,
            "bundle": {"artifact_bytes": b"Useful approved text", "artifact_media_type": "text/plain"},
        }
        self.pointer = self.verified["pointer"]
        self._write_permissions("review", "drafts-only", enabled=True)
        self._write_config()

    def _write_permissions(self, publish: str, mode: str, *, enabled: bool) -> None:
        (self.workspace / "runtime/permissions.json").write_text(json.dumps({
            "publish": publish,
            "agents": {"publisher": {"enabled": enabled}},
            "connectors": {"postiz": {"enabled": enabled, "mode": mode}},
        }), encoding="utf-8")
        (self.workspace / "runtime/permissions.json").chmod(0o600)

    def _write_config(self) -> None:
        (self.workspace / "runtime/postiz.json").write_text(json.dumps({
            "base_url": "http://127.0.0.1:5000/api/public/v1",
            "integrations": {"x": {
                "enabled": True,
                "id": "private-integration-id",
                "provider": "x",
                "settings": {"__type": "x"},
            }},
        }), encoding="utf-8")
        (self.workspace / "runtime/postiz.json").chmod(0o600)

    def _deliver(self, **overrides):
        values = {
            "release_package": self.pointer,
            "platform": "x",
            "operation": "draft",
            "requested_at": "2026-09-07T12:00:00Z",
            "environment": {"POSTIZ_API_KEY": "test-only-key"},
        }
        values.update(overrides)
        return postiz_connector.deliver(self.workspace, self.profile_path, **values)

    def test_disabled_connector_stops_before_transport_or_writes(self) -> None:
        self._write_permissions("review", "drafts-only", enabled=False)
        transport = mock.Mock()
        with mock.patch.object(postiz_connector.release_core, "load_verified_release_package", return_value=self.verified), \
             mock.patch.object(postiz_connector.release_core, "_apply_request") as apply:
            with self.assertRaisesRegex(postiz_connector.PostizConnectorError, "not enabled"):
                self._deliver(transport=transport)
        transport.assert_not_called()
        apply.assert_not_called()

    def test_review_mode_allows_draft_but_not_schedule(self) -> None:
        self._write_permissions("review", "schedule", enabled=True)
        transport = mock.Mock()
        with mock.patch.object(postiz_connector.release_core, "load_verified_release_package", return_value=self.verified), \
             mock.patch.object(postiz_connector.release_core, "_apply_request") as apply:
            with self.assertRaisesRegex(postiz_connector.PostizConnectorError, "require automatic"):
                self._deliver(operation="schedule", scheduled_at="2026-09-08T12:00:00Z", transport=transport)
        transport.assert_not_called()
        apply.assert_not_called()

    def test_schedule_must_be_after_the_request(self) -> None:
        self._write_permissions("automatic", "schedule", enabled=True)
        with self.assertRaisesRegex(postiz_connector.PostizConnectorError, "later"):
            self._deliver(
                operation="schedule",
                scheduled_at="2026-09-07T11:00:00Z",
                transport=mock.Mock(),
            )

    def test_exact_approved_text_is_sent_once_and_receipted_without_key(self) -> None:
        calls: list[tuple[str, str, bytes]] = []
        writes: list[dict] = []

        def transport(url: str, key: str, payload: bytes):
            calls.append((url, key, payload))
            return [{"postId": "post-123", "integration": "private-integration-id"}]

        with mock.patch.object(postiz_connector.release_core, "load_verified_release_package", return_value=self.verified), \
             mock.patch.object(postiz_connector.release_core, "_state_bytes", return_value=None), \
             mock.patch.object(postiz_connector.release_core, "_apply_request", side_effect=lambda _w, _p, value: writes.append(value) or {"status": "completed"}):
            result = self._deliver(transport=transport)

        self.assertEqual(result["status"], "drafted")
        self.assertFalse(result["publicly_live"])
        self.assertEqual(len(calls), 1)
        payload = json.loads(calls[0][2])
        self.assertEqual(payload["type"], "draft")
        self.assertEqual(payload["posts"][0]["value"][0]["content"], "Useful approved text")
        self.assertEqual(len(writes), 2)
        persisted = json.dumps(writes, sort_keys=True)
        self.assertNotIn("test-only-key", persisted)
        self.assertNotIn("private-integration-id", persisted)

    def test_missing_key_does_not_create_an_intent(self) -> None:
        with mock.patch.object(postiz_connector.release_core, "load_verified_release_package", return_value=self.verified), \
             mock.patch.object(postiz_connector.release_core, "_apply_request") as apply:
            with self.assertRaisesRegex(postiz_connector.PostizConnectorError, "API_KEY is unavailable"):
                self._deliver(environment={})
        apply.assert_not_called()

    def test_exact_reviewed_media_is_uploaded_before_post_and_receipted(self) -> None:
        media = {
            "material_id": "material-1", "name": "frame.png",
            "mime_type": "image/png", "content": b"exact-png",
            "content_sha256": hashlib.sha256(b"exact-png").hexdigest(),
        }
        writes: list[dict] = []
        upload = mock.Mock(return_value={"id": "media-1", "path": "https://uploads.example/frame.png"})
        post = mock.Mock(return_value=[{"postId": "post-1", "integration": "private-integration-id"}])
        with mock.patch.object(postiz_connector.release_core, "load_verified_release_package", return_value=self.verified), \
             mock.patch.object(postiz_connector, "_approved_media", return_value=[media]), \
             mock.patch.object(postiz_connector.release_core, "_state_bytes", return_value=None), \
             mock.patch.object(postiz_connector.release_core, "_apply_request", side_effect=lambda _w, _p, value: writes.append(value) or {"status": "completed"}):
            result = self._deliver(transport=post, media_transport=upload)
        self.assertEqual(result["status"], "drafted")
        upload.assert_called_once_with(
            "http://127.0.0.1:5000/api/public/v1/upload", "test-only-key",
            "frame.png", "image/png", b"exact-png")
        payload = json.loads(post.call_args.args[2])
        self.assertEqual(payload["posts"][0]["value"][0]["image"], [
            {"id": "media-1", "path": "https://uploads.example/frame.png"}
        ])
        persisted = json.dumps(writes, sort_keys=True)
        self.assertIn(media["content_sha256"], persisted)
        self.assertNotIn("exact-png", persisted)

    def test_unsupported_reviewed_media_requires_conversion(self) -> None:
        descriptor = {
            "material_id": "material-1", "record_ref": "records/studio/material-1.json",
            "record_sha256": "a" * 64, "name": "voice.mp3", "mime_type": "audio/mpeg",
            "size_bytes": 3, "content_sha256": hashlib.sha256(b"ID3").hexdigest(),
            "processing_status": "indexed",
        }
        packet = json.dumps({
            "material_packet_version": "1.0", "project_id": "example-project",
            "project_profile_revision": "example-v1", "channel": "x",
            "materials": [descriptor],
        }).encode()
        verified = {**self.verified, "bundle": {
            **self.verified["bundle"], "source_run_id": "qa-task",
            "review": {"evidence": [{"ref": "evidence/packets/broker-materials/task/r1.json"}]},
        }}
        with mock.patch.object(postiz_connector.release_core, "_state_bytes", return_value=packet), \
             mock.patch.object(postiz_connector.release_core, "_receipt_covering"), \
             mock.patch("pipeline.studio.material_asset", return_value={**descriptor, "content": b"ID3"}), \
             self.assertRaisesRegex(postiz_connector.PostizConnectorError, "needs local conversion"):
            postiz_connector._approved_media(verified)

    def test_private_configuration_rejects_nested_credentials(self) -> None:
        value = json.loads((self.workspace / "runtime/postiz.json").read_text())
        value["integrations"]["x"]["settings"]["advanced"] = {"access_token": "wrong-place"}
        (self.workspace / "runtime/postiz.json").write_text(json.dumps(value), encoding="utf-8")
        (self.workspace / "runtime/postiz.json").chmod(0o600)
        with self.assertRaisesRegex(postiz_connector.PostizConnectorError, "must not contain credentials"):
            postiz_connector._configuration(self.workspace, "x", "draft")

    def test_existing_intent_without_receipt_never_repeats_external_request(self) -> None:
        stored: dict[str, bytes] = {}
        transport = mock.Mock(side_effect=postiz_connector.PostizConnectorError("network uncertain"))

        def state(_workspace, _profile, ref, *, missing_ok=False):
            return stored.get(ref)

        def apply(_workspace, _profile, value):
            for item in value["writes"]:
                stored[item["path"]] = item["content"].encode("utf-8")
            return {"status": "completed"}

        with mock.patch.object(postiz_connector.release_core, "load_verified_release_package", return_value=self.verified), \
             mock.patch.object(postiz_connector.release_core, "_state_bytes", side_effect=state), \
             mock.patch.object(postiz_connector.release_core, "_receipt_covering", return_value=({}, None, None)), \
             mock.patch.object(postiz_connector.release_core, "_apply_request", side_effect=apply):
            with self.assertRaisesRegex(postiz_connector.PostizConnectorError, "network uncertain"):
                self._deliver(transport=transport)
            with self.assertRaisesRegex(postiz_connector.PostizConnectorError, "requires reconciliation"):
                self._deliver(transport=transport)
        self.assertEqual(transport.call_count, 1)


if __name__ == "__main__":
    unittest.main()
