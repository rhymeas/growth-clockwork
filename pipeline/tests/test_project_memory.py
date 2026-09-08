from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from pipeline.project_memory import (
    ProjectMemoryError,
    append_memory_record,
    query_project_memory,
    query_project_memory_fts,
    rebuild_memory_index,
    validate_memory_record,
)


class ProjectMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name) / "workspace"
        self.workspace.mkdir()
        (self.workspace / "AGENTS.md").write_text("# Test\n", encoding="utf-8")
        self.profile_root = self.workspace / "projects" / "alpha"
        schemas = self.profile_root / "schemas"
        schemas.mkdir(parents=True)
        source_schema = (
            Path(__file__).resolve().parents[2]
            / "contracts"
            / "schemas"
            / "project-memory-record.schema.json"
        )
        schema_bytes = source_schema.read_bytes()
        schema_path = schemas / "project-memory-record.schema.json"
        schema_path.write_bytes(schema_bytes)
        self.registry = self.profile_root / "schema-registry.json"
        self.registry.write_text(
            json.dumps(
                {
                    "registry_version": "1.0",
                    "schemas": {
                        "project-memory-record@1": {
                            "path": "schemas/project-memory-record.schema.json",
                            "sha256": hashlib.sha256(schema_bytes).hexdigest(),
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        self.profile = self.profile_root / "project.json"
        self._write_profile("alpha-profile-v1")
        self.state = self.profile_root / "state"
        evidence = self.state / "records" / "evidence.json"
        evidence.parent.mkdir(parents=True)
        evidence.write_text('{"source":"synthetic"}\n', encoding="utf-8")
        self.evidence_hash = hashlib.sha256(evidence.read_bytes()).hexdigest()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_profile(self, revision: str) -> None:
        self.profile.write_text(
            json.dumps(
                {
                    "profile_version": "1.0",
                    "profile_revision": revision,
                    "project_id": "alpha-project",
                    "root_role_id": "growth-clockwork-root",
                    "schema_registry": "schema-registry.json",
                    "writer": {
                        "state_root": "projects/alpha/state",
                        "allowed_roots": [
                            "clockwork/run-receipts",
                            "memory",
                            "records",
                        ],
                        "append_only_roots": [
                            "clockwork/run-receipts",
                            "memory",
                            "records",
                        ],
                        "receipt_root": "clockwork/run-receipts",
                        "max_files_per_request": 8,
                        "max_total_bytes": 100000,
                    },
                }
            ),
            encoding="utf-8",
        )

    def record(
        self,
        memory_id: str,
        statement: str,
        *,
        evidence_grade: str = "verified",
        decision_eligible: bool = True,
        claim_mode: str = "observation",
        supersedes: list[str] | None = None,
        created_at: str = "2026-08-31T12:00:00Z",
    ) -> dict[str, object]:
        profile = json.loads(self.profile.read_text(encoding="utf-8"))
        return {
            "project_id": "alpha-project",
            "project_profile_revision": profile["profile_revision"],
            "record_version": "1.0",
            "memory_id": memory_id,
            "memory_revision": "r1",
            "memory_type": "learning",
            "claim_mode": claim_mode,
            "statement": statement,
            "tags": ["youtube", "activation"],
            "evidence": [
                {
                    "artifact_ref": "records/evidence.json",
                    "artifact_revision": "r1",
                    "artifact_sha256": self.evidence_hash,
                    "relation": "supports",
                }
            ],
            "evidence_grade": evidence_grade,
            "decision_eligible": decision_eligible,
            "causal_claim": False,
            "experiment_ref": None,
            "supersedes": supersedes or [],
            "confidence": "low" if evidence_grade == "exploratory" else "medium",
            "source_run_id": "RUN-memory-001",
            "source_task_id": "EX-1000-T001",
            "created_by": "workhorse",
            "contains_personal_data": False,
            "valid_from": created_at,
            "expires_at": None,
            "created_at": created_at,
        }

    def test_append_and_query_exact_memory(self) -> None:
        record = self.record(
            "MEM-youtube-friction",
            "YouTube comments in the bounded sample repeatedly mention setup friction.",
        )
        receipt = append_memory_record(
            self.workspace,
            self.profile,
            record,
            run_id="RUN-memory-001",
            idempotency_key="1c838f1c-9314-4cd4-941c-9a09d59e4c28",
        )
        result = query_project_memory(self.profile, "youtube friction")

        self.assertEqual(receipt["status"], "completed")
        self.assertEqual([item["memory_id"] for item in result["matched"]], ["MEM-youtube-friction"])
        self.assertRegex(result["matched"][0]["record_sha256"], r"^[0-9a-f]{64}$")

    def test_exploratory_memory_is_hidden_from_default_decision_query(self) -> None:
        record = self.record(
            "MEM-youtube-signal",
            "An unofficial source suggests a possible YouTube onboarding question.",
            evidence_grade="exploratory",
            decision_eligible=False,
            claim_mode="hypothesis",
        )
        append_memory_record(
            self.workspace,
            self.profile,
            record,
            run_id="RUN-memory-002",
            idempotency_key="0a3b7ad1-f1ea-44fc-83b0-8c456d7bfa8b",
        )

        default = query_project_memory(self.profile, "youtube")
        discovery = query_project_memory(
            self.profile, "youtube", include_exploratory=True
        )

        self.assertEqual(default["matched"], [])
        self.assertEqual(discovery["matched"][0]["evidence_grade"], "exploratory")

    def test_supersession_and_profile_revision_survive_without_rewrite(self) -> None:
        first = self.record(
            "MEM-old-learning",
            "YouTube comments may indicate setup friction in the sampled window.",
        )
        append_memory_record(
            self.workspace,
            self.profile,
            first,
            run_id="RUN-memory-003",
            idempotency_key="3b72dcad-e9f2-40c0-8ad1-3208781ab324",
        )
        second = self.record(
            "MEM-new-learning",
            "YouTube setup friction remains a bounded observation after source review.",
            supersedes=["MEM-old-learning"],
            created_at="2026-08-31T13:00:00Z",
        )
        append_memory_record(
            self.workspace,
            self.profile,
            second,
            run_id="RUN-memory-004",
            idempotency_key="b51ccb72-2ba8-48ef-82d1-a4c829dc16bc",
        )
        self._write_profile("alpha-profile-v2")

        result = query_project_memory(
            self.profile,
            "youtube setup friction",
            now=dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc),
        )

        self.assertEqual([item["memory_id"] for item in result["matched"]], ["MEM-new-learning"])
        self.assertEqual(result["matched"][0]["source_profile_revision"], "alpha-profile-v1")
        self.assertEqual(result["project_profile_revision"], "alpha-profile-v2")

    def test_evidence_hash_mismatch_fails_closed(self) -> None:
        record = self.record(
            "MEM-invalid-evidence",
            "This memory has evidence bytes that do not match the declared hash.",
        )
        record["evidence"][0]["artifact_sha256"] = "0" * 64  # type: ignore[index]

        with self.assertRaisesRegex(ProjectMemoryError, "Evidence hash mismatch"):
            validate_memory_record(self.profile, record)

    def test_exploratory_causal_claim_is_rejected(self) -> None:
        record = self.record(
            "MEM-invalid-causal",
            "An exploratory signal must never become a causal system memory.",
            evidence_grade="exploratory",
            decision_eligible=False,
            claim_mode="experiment_result",
        )
        record["causal_claim"] = True
        record["experiment_ref"] = "records/experiment.json"

        with self.assertRaisesRegex(ProjectMemoryError, "Memory schema rejected"):
            validate_memory_record(self.profile, record, verify_evidence=False)

    def test_rebuildable_fts5_index_returns_exact_canonical_pointer(self) -> None:
        record = self.record(
            "MEM-fts-learning",
            "YouTube activation research found a bounded setup-friction pattern.",
        )
        append_memory_record(
            self.workspace,
            self.profile,
            record,
            run_id="RUN-memory-005",
            idempotency_key="be9cf36a-5117-468d-a610-f6b22d66a53d",
        )
        cache = Path(self.temporary.name) / "derived" / "memory.sqlite3"

        receipt = rebuild_memory_index(self.profile, index_path=cache)
        result = query_project_memory_fts(
            self.profile, "youtube friction", index_path=cache
        )

        self.assertFalse(receipt["canonical"])
        self.assertTrue(receipt["rebuildable"])
        self.assertEqual(result["matched"][0]["memory_id"], "MEM-fts-learning")
        self.assertRegex(result["matched"][0]["record_sha256"], r"^[0-9a-f]{64}$")

    def test_fts5_index_fails_closed_when_canonical_memory_changes(self) -> None:
        first = self.record(
            "MEM-fts-old",
            "The first bounded YouTube activation observation is recorded.",
        )
        append_memory_record(
            self.workspace,
            self.profile,
            first,
            run_id="RUN-memory-006",
            idempotency_key="95f96688-41de-4327-abf9-f50b6e16725f",
        )
        cache = Path(self.temporary.name) / "derived" / "memory.sqlite3"
        rebuild_memory_index(self.profile, index_path=cache)
        second = self.record(
            "MEM-fts-new",
            "A later bounded YouTube activation observation changes the source set.",
            created_at="2026-08-31T13:00:00Z",
        )
        append_memory_record(
            self.workspace,
            self.profile,
            second,
            run_id="RUN-memory-007",
            idempotency_key="e9db46e7-912f-4463-a151-467df39aac17",
        )

        with self.assertRaisesRegex(ProjectMemoryError, "stale"):
            query_project_memory_fts(
                self.profile, "youtube", index_path=cache
            )


if __name__ == "__main__":
    unittest.main()
