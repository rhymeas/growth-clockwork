from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from pipeline.research_adapters import (
    ResearchAdapterError,
    load_research_adapter_registry,
    plan_research_adapters,
    prepare_research_observation,
)


SOURCE = Path(__file__).resolve().parents[2]


class ResearchAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name) / "workspace"
        self.workspace.mkdir()
        (self.workspace / "AGENTS.md").write_text("# Test\n", encoding="utf-8")
        (self.workspace / "contracts").mkdir()
        source = SOURCE / "contracts/research-adapter-registry.json"
        target = self.workspace / "contracts/research-adapter-registry.json"
        shutil.copyfile(source, target)
        self.digest = hashlib.sha256(target.read_bytes()).hexdigest()
        self.profile_root = self.workspace / "projects/alpha"
        self.profile_root.mkdir(parents=True)
        schemas = self.profile_root / "schemas"
        schemas.mkdir()
        schema_source = SOURCE / "contracts/schemas/platform-observation.schema.json"
        schema_target = schemas / "platform-observation.schema.json"
        shutil.copyfile(schema_source, schema_target)
        (self.profile_root / "schema-registry.json").write_text(
            json.dumps(
                {
                    "registry_version": "1.0",
                    "schemas": {
                        "platform-observation@1": {
                            "path": "schemas/platform-observation.schema.json",
                            "sha256": hashlib.sha256(
                                schema_target.read_bytes()
                            ).hexdigest(),
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        self.profile = self.profile_root / "project.json"
        self._write_profile(self.digest)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_profile(self, digest: str) -> None:
        self.profile.write_text(
            json.dumps(
                {
                    "profile_version": "1.0",
                    "profile_revision": "alpha-profile-v1",
                    "project_id": "alpha-project",
                    "root_role_id": "growth-clockwork-root",
                    "schema_registry": "schema-registry.json",
                    "research_adapters": {
                        "artifact_ref": "contracts/research-adapter-registry.json",
                        "artifact_revision": "research-adapters-2026-08-31-r1",
                        "artifact_sha256": digest,
                    },
                    "writer": {
                        "state_root": "projects/alpha/state",
                        "allowed_roots": ["clockwork/run-receipts", "records"],
                        "append_only_roots": ["clockwork/run-receipts", "records"],
                        "receipt_root": "clockwork/run-receipts",
                        "max_files_per_request": 8,
                        "max_total_bytes": 100000,
                    },
                }
            ),
            encoding="utf-8",
        )

    def _manual_observation(self) -> tuple[dict[str, object], dict[str, bytes]]:
        evidence_ref = "records/evidence/OBS-youtube-manual-authority.json"
        evidence = b'{"authorized":true,"raw_personal_data_stored":false}\n'
        observation: dict[str, object] = {
            "project_id": "alpha-project",
            "project_profile_revision": "alpha-profile-v1",
            "observation_version": "1.0",
            "observation_id": "OBS-youtube-manual",
            "platform": "youtube",
            "surface": "one operator-supplied public video observation",
            "research_question": "Which stated setup problem appears in this bounded supplied item?",
            "access_method": "manual_public_observation",
            "source_tier": "documented_oss_capture",
            "governance_use": "directional_discovery",
            "collector_tool": "manual-evidence-intake@1",
            "access_terms_ref": "https://www.youtube.com/static?template=terms",
            "collection_authority_ref": evidence_ref,
            "query_or_sampling_frame": {"supplied_items": 1},
            "observed_window": {"start": None, "end": None},
            "sample": {
                "items_seen": 1,
                "items_included": 1,
                "pages_fetched": 0,
                "pagination_complete": False,
                "quota_limited": False,
                "representativeness": "not_claimed",
            },
            "content_dimensions": ["pains", "language"],
            "evidence": [
                {
                    "artifact_ref": evidence_ref,
                    "artifact_revision": "r1",
                    "artifact_sha256": hashlib.sha256(evidence).hexdigest(),
                }
            ],
            "commercial_use_clearance": "cleared",
            "raw_personal_data_stored": False,
            "redaction_method": "identifiers_removed",
            "retention_policy": "Retain only the redacted supplied evidence receipt.",
            "known_biases": ["Operator selection and single-item sampling bias."],
            "unknowns": ["No population representativeness is claimed."],
            "collected_at": "2026-08-31T12:00:00Z",
        }
        return observation, {evidence_ref: evidence}

    def test_exact_registry_loads_and_manual_lane_is_dispatchable(self) -> None:
        registry = load_research_adapter_registry(self.workspace, self.profile)
        plan = plan_research_adapters(
            self.workspace,
            self.profile,
            platform="youtube",
            access_class="manual_evidence",
        )

        self.assertGreaterEqual(len(registry.adapters), 10)
        self.assertTrue(plan["dispatchable"])
        self.assertEqual(
            [item["adapter_id"] for item in plan["candidates"]],
            ["manual-evidence-intake"],
        )
        self.assertFalse(plan["executed"])

    def test_official_api_is_visible_but_not_falsely_runnable(self) -> None:
        blocked = plan_research_adapters(
            self.workspace,
            self.profile,
            platform="youtube",
            access_class="official_public",
        )
        visible = plan_research_adapters(
            self.workspace,
            self.profile,
            platform="youtube",
            access_class="official_public",
            require_implemented=False,
        )

        self.assertEqual(blocked["candidates"], [])
        self.assertFalse(blocked["dispatchable"])
        self.assertIn("youtube-data-api", [item["adapter_id"] for item in visible["candidates"]])
        self.assertFalse(visible["dispatchable"])

    def test_unofficial_sources_require_explicit_discovery_lane(self) -> None:
        with self.assertRaisesRegex(ResearchAdapterError, "exploratory"):
            plan_research_adapters(
                self.workspace,
                self.profile,
                platform="tiktok",
                access_class="unofficial_modelled",
                require_implemented=False,
            )
        plan = plan_research_adapters(
            self.workspace,
            self.profile,
            platform="tiktok",
            access_class="unofficial_modelled",
            require_implemented=False,
            allow_exploratory=True,
        )
        self.assertEqual(
            plan["candidates"][0]["allowed_governance_uses"],
            ["idea_generation_only"],
        )
        self.assertFalse(plan["candidates"][0]["canonical_output"])

    def test_hash_mismatch_fails_closed(self) -> None:
        self._write_profile("0" * 64)
        with self.assertRaisesRegex(ResearchAdapterError, "hash mismatch"):
            load_research_adapter_registry(self.workspace, self.profile)

    def test_dispatches_real_manual_intake_entrypoint_without_side_effects(self) -> None:
        observation, evidence = self._manual_observation()

        result = prepare_research_observation(
            self.workspace,
            self.profile,
            adapter_id="manual-evidence-intake",
            observation=observation,
            evidence_artifacts=evidence,
        )

        self.assertEqual(result.adapter_id, "manual-evidence-intake@1")
        self.assertEqual(json.loads(result.output_content), observation)
        self.assertFalse((self.profile_root / "state").exists())

    def test_contract_only_adapter_cannot_be_dispatched(self) -> None:
        observation, evidence = self._manual_observation()

        with self.assertRaisesRegex(ResearchAdapterError, "not executable"):
            prepare_research_observation(
                self.workspace,
                self.profile,
                adapter_id="youtube-data-api",
                observation=observation,
                evidence_artifacts=evidence,
            )

    def test_registry_cannot_claim_an_unimplemented_entrypoint(self) -> None:
        registry_path = self.workspace / "contracts/research-adapter-registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        youtube = next(
            item
            for item in registry["adapters"]
            if item["adapter_id"] == "youtube-data-api"
        )
        youtube["implementation_status"] = "implemented"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        self._write_profile(hashlib.sha256(registry_path.read_bytes()).hexdigest())

        with self.assertRaisesRegex(
            ResearchAdapterError, "executable entrypoints differ"
        ):
            load_research_adapter_registry(self.workspace, self.profile)


if __name__ == "__main__":
    unittest.main()
