from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from pipeline import root_writer
from pipeline.root_writer import WriterError, apply_request, apply_request_value


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class RootWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "project"
        self.workspace.mkdir()
        (self.workspace / "AGENTS.md").write_text("# Test\n", encoding="utf-8")
        self.profile = self.workspace / "projects/test-profile/project.json"
        self.profile.parent.mkdir(parents=True)
        self.profile.write_text(
            json.dumps(
                {
                    "profile_version": "1.0",
                    "profile_revision": "test-profile-v1",
                    "project_id": "test-project",
                    "root_role_id": "test-root",
                    "writer": {
                        "state_root": "projects/test-profile/state",
                        "allowed_roots": [
                            "clockwork/run-receipts",
                            "drafts",
                            "records",
                        ],
                        "append_only_roots": [
                            "clockwork/run-receipts",
                            "records",
                        ],
                        "receipt_root": "clockwork/run-receipts",
                        "max_files_per_request": 4,
                        "max_total_bytes": 10000,
                    },
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def request(
        self,
        writes: list[dict[str, object]],
        *,
        key: str | None = None,
        project_id: str = "test-project",
        preconditions: list[dict[str, object]] | None = None,
    ) -> Path:
        path = Path(self.temp.name) / f"request-{uuid.uuid4()}.json"
        payload: dict[str, object] = {
            "writer_request_version": "1.0",
            "project_id": project_id,
            "project_profile_revision": "test-profile-v1",
            "run_id": "run-20260831-001",
            "idempotency_key": key or str(uuid.uuid4()),
            "requested_by": "test-root",
            "writes": writes,
        }
        if preconditions is not None:
            payload["preconditions"] = preconditions
        path.write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        return path

    def create_write(self, path: str, content: str) -> dict[str, object]:
        return {
            "path": path,
            "mode": "create",
            "content": content,
            "content_sha256": digest(content),
            "expected_sha256": None,
        }

    @staticmethod
    def precondition(
        path: str, expected_sha256: str | None
    ) -> dict[str, object]:
        return {"path": path, "expected_sha256": expected_sha256}

    def test_create_and_idempotent_replay(self) -> None:
        key = str(uuid.uuid4())
        request = self.request(
            [self.create_write("drafts/lesson.md", "useful lesson\n")], key=key
        )
        first = apply_request(self.workspace, self.profile, request)
        second = apply_request(self.workspace, self.profile, request)
        self.assertEqual(first, second)
        self.assertEqual(
            (
                self.workspace
                / "projects/test-profile/state/drafts/lesson.md"
            ).read_text(encoding="utf-8"),
            "useful lesson\n",
        )
        receipt = (
            self.workspace
            / "projects/test-profile/state/clockwork/run-receipts/run-20260831-001"
            / f"writer-{key}.json"
        )
        self.assertTrue(receipt.is_file())

    def test_in_memory_request_uses_same_writer_without_temp_request(self) -> None:
        request_path = self.request(
            [self.create_write("records/in-memory.txt", "private input\n")]
        )
        request = json.loads(request_path.read_text())
        request_path.unlink()
        receipt = apply_request_value(self.workspace, self.profile, request)
        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(
            (self.workspace / "projects/test-profile/state/records/in-memory.txt").read_text(),
            "private input\n",
        )

    def test_absent_and_exact_hash_preconditions_pass(self) -> None:
        source = self.workspace / "projects/test-profile/state/records/source.json"
        source.parent.mkdir(parents=True)
        source.write_text("exact source bytes\n", encoding="utf-8")
        request = self.request(
            [self.create_write("drafts/result.md", "result\n")],
            preconditions=[
                self.precondition("drafts/future-action.json", None),
                self.precondition(
                    "records/source.json", digest("exact source bytes\n")
                ),
            ],
        )

        receipt = apply_request(self.workspace, self.profile, request)

        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(
            (
                self.workspace
                / "projects/test-profile/state/drafts/result.md"
            ).read_text(encoding="utf-8"),
            "result\n",
        )

    def test_precondition_violation_writes_nothing(self) -> None:
        state = self.workspace / "projects/test-profile/state"
        guard = state / "records/guard.json"
        guard.parent.mkdir(parents=True)
        guard.write_text("occupied\n", encoding="utf-8")

        cases = [
            self.precondition("records/guard.json", None),
            self.precondition("records/guard.json", "0" * 64),
        ]
        for index, precondition in enumerate(cases):
            with self.subTest(precondition=precondition):
                output = f"drafts/blocked-{index}.md"
                key = str(uuid.uuid4())
                request = self.request(
                    [self.create_write(output, "must not exist\n")],
                    key=key,
                    preconditions=[precondition],
                )
                with self.assertRaisesRegex(WriterError, "Precondition failed"):
                    apply_request(self.workspace, self.profile, request)

                self.assertFalse((state / output).exists())
                receipt = (
                    state
                    / "clockwork/run-receipts/run-20260831-001"
                    / f"writer-{key}.json"
                )
                self.assertFalse(receipt.exists())

    def test_preconditions_reject_unsafe_duplicate_and_symlink_paths(self) -> None:
        cases = [
            [self.precondition("../outside.json", None)],
            [self.precondition("/tmp/outside.json", None)],
            [self.precondition("facts/approved.md", None)],
            [self.precondition("records", None)],
            [
                self.precondition(
                    "clockwork/run-receipts/run-20260831-001/forged.json",
                    None,
                )
            ],
            [
                self.precondition("drafts/duplicate.json", None),
                self.precondition("drafts/duplicate.json", None),
            ],
            [{"path": "drafts/missing-hash.json"}],
            [
                {
                    "path": "drafts/extra-key.json",
                    "expected_sha256": None,
                    "extra": True,
                }
            ],
            [self.precondition("drafts/invalid-hash.json", "not-a-sha256")],
        ]
        for preconditions in cases:
            with self.subTest(preconditions=preconditions):
                request = self.request(
                    [self.create_write("drafts/safe.md", "blocked\n")],
                    preconditions=preconditions,
                )
                with self.assertRaises(WriterError):
                    apply_request(self.workspace, self.profile, request)

        state = self.workspace / "projects/test-profile/state"
        outside = Path(self.temp.name) / "outside-precondition"
        outside.mkdir()
        (state / "drafts").symlink_to(outside, target_is_directory=True)
        request = self.request(
            [self.create_write("records/safe.json", "blocked\n")],
            preconditions=[self.precondition("drafts/guard.json", None)],
        )
        with self.assertRaisesRegex(WriterError, "Symlink path"):
            apply_request(self.workspace, self.profile, request)
        self.assertFalse((state / "records/safe.json").exists())

    def test_idempotent_receipt_replay_precedes_precondition_recheck(self) -> None:
        key = str(uuid.uuid4())
        request = self.request(
            [self.create_write("drafts/result.md", "result\n")],
            key=key,
            preconditions=[self.precondition("records/followup.json", None)],
        )
        first = apply_request(self.workspace, self.profile, request)
        apply_request(
            self.workspace,
            self.profile,
            self.request(
                [self.create_write("records/followup.json", "later\n")]
            ),
        )

        replay = apply_request(self.workspace, self.profile, request)

        self.assertEqual(replay, first)

    def test_same_idempotency_key_cannot_change_request(self) -> None:
        key = str(uuid.uuid4())
        first = self.request([self.create_write("drafts/a.md", "one")], key=key)
        apply_request(self.workspace, self.profile, first)
        second = self.request([self.create_write("drafts/b.md", "two")], key=key)
        with self.assertRaisesRegex(WriterError, "another request"):
            apply_request(self.workspace, self.profile, second)

    def test_corrupt_idempotent_receipt_is_rejected(self) -> None:
        key = str(uuid.uuid4())
        request = self.request(
            [self.create_write("drafts/a.md", "one")],
            key=key,
        )
        apply_request(self.workspace, self.profile, request)
        receipt = (
            self.workspace
            / "projects/test-profile/state/clockwork/run-receipts"
            / "run-20260831-001"
            / f"writer-{key}.json"
        )
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        payload["project_id"] = "other-project"
        receipt.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(WriterError, "identity mismatch"):
            apply_request(self.workspace, self.profile, request)

    def test_rejects_traversal_and_protected_paths(self) -> None:
        for unsafe in ("../outside.txt", "/tmp/outside.txt", ".git/config"):
            with self.subTest(path=unsafe):
                request = self.request([self.create_write(unsafe, "blocked")])
                with self.assertRaises(WriterError):
                    apply_request(self.workspace, self.profile, request)
        request = self.request([self.create_write("facts/approved.md", "blocked")])
        with self.assertRaisesRegex(WriterError, "outside the project allowlist"):
            apply_request(self.workspace, self.profile, request)

    def test_revisions_require_a_new_path(self) -> None:
        target = self.workspace / "projects/test-profile/state/records/GV-0001.json"
        target.parent.mkdir(parents=True)
        target.write_text("old", encoding="utf-8")
        request = self.request(
            [
                {
                    "path": "records/GV-0001.json",
                    "mode": "replace",
                    "content": "new",
                    "content_sha256": digest("new"),
                    "expected_sha256": digest("old"),
                }
            ]
        )
        with self.assertRaisesRegex(WriterError, "revisions require a new path"):
            apply_request(self.workspace, self.profile, request)

    def test_receipt_namespace_is_writer_owned(self) -> None:
        request = self.request(
            [
                self.create_write(
                    "clockwork/run-receipts/run-20260831-001/fake.json",
                    "blocked",
                )
            ]
        )
        with self.assertRaisesRegex(WriterError, "receipt root is reserved"):
            apply_request(self.workspace, self.profile, request)

    def test_create_rejects_target_created_after_preflight(self) -> None:
        target = self.workspace / "projects/test-profile/state/drafts/race.md"
        original = root_writer.validate_request

        def target_appears(request, profile, workspace, **kwargs):
            prepared = original(request, profile, workspace, **kwargs)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("concurrent", encoding="utf-8")
            return prepared

        request = self.request([self.create_write("drafts/race.md", "writer")])
        with patch.object(root_writer, "validate_request", target_appears):
            with self.assertRaisesRegex(
                WriterError, "already exists|differs from the requested bytes"
            ):
                apply_request(self.workspace, self.profile, request)

        self.assertEqual(target.read_text(encoding="utf-8"), "concurrent")

    def test_commit_rejects_ancestor_symlink_swap_after_preflight(self) -> None:
        state = self.workspace / "projects/test-profile/state"
        drafts = state / "drafts"
        drafts.mkdir(parents=True)
        outside = Path(self.temp.name) / "outside-race"
        outside.mkdir()
        original = root_writer.validate_request

        def ancestor_becomes_symlink(request, profile, workspace, **kwargs):
            prepared = original(request, profile, workspace, **kwargs)
            drafts.rmdir()
            drafts.symlink_to(outside, target_is_directory=True)
            return prepared

        request = self.request([self.create_write("drafts/race.md", "writer")])
        with patch.object(root_writer, "validate_request", ancestor_becomes_symlink):
            with self.assertRaisesRegex(WriterError, "Unsafe or unreadable"):
                apply_request(self.workspace, self.profile, request)

        self.assertFalse((outside / "race.md").exists())

    def test_receipt_created_after_preflight_is_not_overwritten(self) -> None:
        original = root_writer._publish_exclusive
        key = str(uuid.uuid4())
        receipt = (
            self.workspace
            / "projects/test-profile/state/clockwork/run-receipts"
            / "run-20260831-001"
            / f"writer-{key}.json"
        )

        def receipt_appears(state_fd, transaction_fd, relative, content):
            if relative.parts[:2] == ("clockwork", "run-receipts"):
                receipt.parent.mkdir(parents=True, exist_ok=True)
                receipt.write_text("concurrent", encoding="utf-8")
            return original(state_fd, transaction_fd, relative, content)

        request = self.request(
            [self.create_write("drafts/result.md", "writer")],
            key=key,
        )
        with patch.object(root_writer, "_publish_exclusive", receipt_appears):
            with self.assertRaisesRegex(WriterError, "no completed receipt"):
                apply_request(self.workspace, self.profile, request)

        self.assertEqual(receipt.read_text(encoding="utf-8"), "concurrent")

    def test_stale_transaction_stage_is_scoped_and_cleaned(self) -> None:
        transaction = (
            self.workspace / "projects/test-profile/state/.writer-tx"
        )
        transaction.mkdir(parents=True)
        stale = transaction / ("a" * 32 + ".tmp")
        stale.write_text("stale private content", encoding="utf-8")

        request = self.request([self.create_write("drafts/result.md", "writer")])
        apply_request(self.workspace, self.profile, request)

        self.assertFalse(stale.exists())
        self.assertEqual(transaction.stat().st_mode & 0o777, 0o700)

    def test_replay_converges_after_partial_byte_identical_write(self) -> None:
        request = self.request(
            [
                self.create_write("records/partial/first.json", "first\n"),
                self.create_write("records/partial/second.json", "second\n"),
            ]
        )
        original = root_writer._publish_exclusive
        calls = 0

        def fail_second_data_write(state_fd, transaction_fd, relative, content):
            nonlocal calls
            if relative.parts[0] != "clockwork":
                calls += 1
                if calls == 2:
                    raise WriterError("simulated interruption")
            return original(state_fd, transaction_fd, relative, content)

        with patch.object(root_writer, "_publish_exclusive", fail_second_data_write):
            with self.assertRaisesRegex(WriterError, "no completed receipt"):
                apply_request(self.workspace, self.profile, request)

        receipt = apply_request(self.workspace, self.profile, request)

        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(
            (
                self.workspace
                / "projects/test-profile/state/records/partial/first.json"
            ).read_text(encoding="utf-8"),
            "first\n",
        )
        self.assertEqual(
            (
                self.workspace
                / "projects/test-profile/state/records/partial/second.json"
            ).read_text(encoding="utf-8"),
            "second\n",
        )

    def test_recovery_rejects_different_existing_bytes(self) -> None:
        target = self.workspace / "projects/test-profile/state/records/conflict.json"
        target.parent.mkdir(parents=True)
        target.write_text("different\n", encoding="utf-8")
        request = self.request(
            [self.create_write("records/conflict.json", "requested\n")]
        )

        with self.assertRaisesRegex(WriterError, "differs from the requested bytes"):
            apply_request(self.workspace, self.profile, request)

    def test_rejects_content_hash_mismatch(self) -> None:
        item = self.create_write("drafts/lesson.md", "content")
        item["content_sha256"] = "0" * 64
        with self.assertRaisesRegex(WriterError, "Content hash mismatch"):
            apply_request(self.workspace, self.profile, self.request([item]))

    def test_invalid_mode_type_is_a_clean_rejection(self) -> None:
        item = self.create_write("drafts/lesson.md", "content")
        item["mode"] = ["create"]
        with self.assertRaisesRegex(WriterError, "mode must be create"):
            apply_request(self.workspace, self.profile, self.request([item]))

    def test_invalid_utf8_content_is_a_clean_rejection(self) -> None:
        item = self.create_write("drafts/lesson.md", "content")
        item["content"] = "\ud800"
        item["content_sha256"] = "0" * 64
        with self.assertRaisesRegex(WriterError, "canonical UTF-8"):
            apply_request(self.workspace, self.profile, self.request([item]))

    def test_non_directory_ancestor_is_rejected(self) -> None:
        state = self.workspace / "projects/test-profile/state"
        state.mkdir(parents=True)
        (state / "drafts").write_text("not a directory", encoding="utf-8")
        request = self.request(
            [self.create_write("drafts/lesson.md", "blocked")]
        )
        with self.assertRaisesRegex(WriterError, "Non-directory path ancestor"):
            apply_request(self.workspace, self.profile, request)

    def test_rejects_symlink_path(self) -> None:
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        state = self.workspace / "projects/test-profile/state"
        state.mkdir(parents=True)
        (state / "drafts").symlink_to(outside, target_is_directory=True)
        request = self.request([self.create_write("drafts/escape.md", "blocked")])
        with self.assertRaisesRegex(WriterError, "Symlink path"):
            apply_request(self.workspace, self.profile, request)

    def test_rejects_wrong_project(self) -> None:
        request = self.request(
            [self.create_write("drafts/lesson.md", "content")],
            project_id="another-project",
        )
        with self.assertRaisesRegex(WriterError, "does not match"):
            apply_request(self.workspace, self.profile, request)

    def test_rejects_stale_project_profile_revision(self) -> None:
        request = self.request([self.create_write("drafts/lesson.md", "content")])
        payload = json.loads(request.read_text(encoding="utf-8"))
        payload["project_profile_revision"] = "test-profile-v0"
        request.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(WriterError, "stale or mismatched"):
            apply_request(self.workspace, self.profile, request)

    def test_malformed_header_is_a_clean_rejection(self) -> None:
        request = Path(self.temp.name) / "malformed.json"
        request.write_text(json.dumps({"project_id": "test-project"}), encoding="utf-8")
        with self.assertRaisesRegex(WriterError, "missing fields"):
            apply_request(self.workspace, self.profile, request)

    def test_rejects_duplicate_paths_and_request_limit(self) -> None:
        duplicate = self.create_write("drafts/a.md", "same")
        with self.assertRaisesRegex(WriterError, "Duplicate write path"):
            apply_request(
                self.workspace, self.profile, self.request([duplicate, duplicate])
            )
        too_many = [
            self.create_write(f"drafts/{index}.md", str(index)) for index in range(5)
        ]
        with self.assertRaisesRegex(WriterError, "max_files"):
            apply_request(self.workspace, self.profile, self.request(too_many))

    def test_profile_switch_keeps_identical_paths_isolated(self) -> None:
        first = self.request([self.create_write("drafts/result.md", "project one")])
        apply_request(self.workspace, self.profile, first)

        second_profile = self.workspace / "projects/second-profile/project.json"
        second_profile.parent.mkdir(parents=True)
        payload = json.loads(self.profile.read_text(encoding="utf-8"))
        payload["profile_revision"] = "second-profile-v1"
        payload["project_id"] = "second-project"
        payload["writer"]["state_root"] = "projects/second-profile/state"
        second_profile.write_text(json.dumps(payload), encoding="utf-8")

        second = json.loads(first.read_text(encoding="utf-8"))
        second["project_id"] = "second-project"
        second["project_profile_revision"] = "second-profile-v1"
        second["idempotency_key"] = str(uuid.uuid4())
        second["writes"][0]["content"] = "project two"
        second["writes"][0]["content_sha256"] = digest("project two")
        second_request = Path(self.temp.name) / "second-request.json"
        second_request.write_text(json.dumps(second), encoding="utf-8")
        apply_request(self.workspace, second_profile, second_request)

        self.assertEqual(
            (
                self.workspace
                / "projects/test-profile/state/drafts/result.md"
            ).read_text(encoding="utf-8"),
            "project one",
        )
        self.assertEqual(
            (
                self.workspace
                / "projects/second-profile/state/drafts/result.md"
            ).read_text(encoding="utf-8"),
            "project two",
        )

    def test_profile_cannot_claim_another_package_state(self) -> None:
        payload = json.loads(self.profile.read_text(encoding="utf-8"))
        payload["writer"]["state_root"] = "projects/other-profile/state"
        self.profile.write_text(json.dumps(payload), encoding="utf-8")

        request = self.request([self.create_write("drafts/result.md", "blocked")])
        with self.assertRaisesRegex(WriterError, "selected profile package"):
            apply_request(self.workspace, self.profile, request)

    def test_profile_accepts_normalized_optional_professional_contracts(self) -> None:
        payload = json.loads(self.profile.read_text(encoding="utf-8"))
        payload["professional_contracts"] = {
            "schema": {
                "artifact_ref": "contracts/professional-deliverable.schema.json",
                "artifact_revision": "professional-deliverable-v1",
                "artifact_sha256": "a" * 64,
            },
            "requirements": {
                "artifact_ref": "contracts/deliverable-requirements.json",
                "artifact_revision": "professional-deliverable-requirements-v1",
                "artifact_sha256": "b" * 64,
            },
        }
        self.profile.write_text(json.dumps(payload), encoding="utf-8")

        profile = root_writer.load_project_profile(self.profile)

        self.assertEqual(
            profile["_professional_contracts"], payload["professional_contracts"]
        )

    def test_profile_rejects_malformed_or_out_of_contracts_professional_pointers(
        self,
    ) -> None:
        base = json.loads(self.profile.read_text(encoding="utf-8"))
        valid = {
            "schema": {
                "artifact_ref": "contracts/professional-deliverable.schema.json",
                "artifact_revision": "professional-deliverable-v1",
                "artifact_sha256": "a" * 64,
            },
            "requirements": {
                "artifact_ref": "contracts/deliverable-requirements.json",
                "artifact_revision": "professional-deliverable-requirements-v1",
                "artifact_sha256": "b" * 64,
            },
        }
        malformed_cases = [
            {"schema": valid["schema"]},
            {
                **valid,
                "requirements": {
                    **valid["requirements"],
                    "artifact_ref": "projects/test-profile/requirements.json",
                },
            },
        ]
        for binding in malformed_cases:
            with self.subTest(binding=binding):
                payload = {**base, "professional_contracts": binding}
                self.profile.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(WriterError):
                    root_writer.load_project_profile(self.profile)


if __name__ == "__main__":
    unittest.main()
