from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from pipeline.audience_research import (
    AudienceResearchError,
    audit_audience_research_package,
    prepare_manual_evidence_observation,
)


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


class AudienceResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.profile_root = Path(self.temporary.name) / "alpha"
        schemas = self.profile_root / "schemas"
        schemas.mkdir(parents=True)
        contracts = Path(__file__).resolve().parents[2] / "contracts" / "schemas"
        registry: dict[str, dict[str, str]] = {}
        for schema_id, filename in (
            ("platform-observation@1", "platform-observation.schema.json"),
            ("audience-research-package@1", "audience-research-package.schema.json"),
        ):
            content = (contracts / filename).read_bytes()
            (schemas / filename).write_bytes(content)
            registry[schema_id] = {
                "path": f"schemas/{filename}",
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        (self.profile_root / "schema-registry.json").write_text(
            json.dumps({"registry_version": "1.0", "schemas": registry}),
            encoding="utf-8",
        )
        self.profile = self.profile_root / "project.json"
        self.profile.write_text(
            json.dumps(
                {
                    "profile_version": "1.0",
                    "profile_revision": "alpha-profile-v1",
                    "project_id": "alpha-project",
                    "root_role_id": "growth-clockwork-root",
                    "schema_registry": "schema-registry.json",
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

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def observation(
        self,
        observation_id: str,
        platform: str,
        *,
        access_method: str = "official_api",
        source_tier: str = "official_primary",
        governance_use: str = "decision_evidence",
        clearance: str = "cleared",
    ) -> dict[str, object]:
        return {
            "project_id": "alpha-project",
            "project_profile_revision": "alpha-profile-v1",
            "observation_version": "1.0",
            "observation_id": observation_id,
            "platform": platform,
            "surface": "public posts and comments",
            "research_question": "Which recurring problem language appears in the bounded sample?",
            "access_method": access_method,
            "source_tier": source_tier,
            "governance_use": governance_use,
            "collector_tool": "synthetic-test-adapter",
            "access_terms_ref": "https://example.test/terms",
            "collection_authority_ref": "records/authority/test.json",
            "query_or_sampling_frame": {"query": "setup friction", "locale": "en"},
            "observed_window": {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-31T00:00:00Z",
            },
            "sample": {
                "items_seen": 20,
                "items_included": 10,
                "pages_fetched": 2,
                "pagination_complete": True,
                "quota_limited": False,
                "representativeness": "not_claimed",
            },
            "content_dimensions": ["pains", "language"],
            "evidence": [
                {
                    "artifact_ref": f"evidence/platform/{observation_id}.json",
                    "artifact_revision": "r1",
                    "artifact_sha256": "1" * 64,
                }
            ],
            "commercial_use_clearance": clearance,
            "raw_personal_data_stored": False,
            "redaction_method": "aggregate_only",
            "retention_policy": "Delete or refresh within the applicable platform deadline.",
            "known_biases": ["Keyword and ranking selection bias."],
            "unknowns": ["The sample is not population-representative."],
            "collected_at": "2026-08-31T12:00:00Z",
        }

    def manual_observation(self) -> tuple[dict[str, object], dict[str, bytes]]:
        observation = self.observation(
            "OBS-youtube-manual",
            "youtube",
            access_method="manual_public_observation",
            source_tier="documented_oss_capture",
            governance_use="directional_discovery",
            clearance="cleared",
        )
        authority_ref = "records/evidence/OBS-youtube-manual-authority.json"
        authority = encoded(
            {
                "authority": "operator-supplied public observation",
                "privacy": "identifiers removed before intake",
                "scope": "one bounded supplied item",
            }
        )
        observation["collector_tool"] = "manual-evidence-intake@1"
        observation["collection_authority_ref"] = authority_ref
        observation["evidence"] = [
            {
                "artifact_ref": authority_ref,
                "artifact_revision": "r1",
                "artifact_sha256": hashlib.sha256(authority).hexdigest(),
            }
        ]
        return observation, {authority_ref: authority}

    def package(
        self,
        observations: list[dict[str, object]],
        *,
        confidence: str = "medium",
        insight_classification: str = "cross_platform",
    ) -> tuple[dict[str, object], dict[str, bytes]]:
        artifacts: dict[str, bytes] = {}
        refs = []
        coverage = []
        platforms = []
        observation_ids = []
        for item in observations:
            platform = str(item["platform"])
            observation_id = str(item["observation_id"])
            ref = f"evidence/platform/{observation_id}.observation.json"
            content = encoded(item)
            artifacts[ref] = content
            refs.append(
                {
                    "observation_id": observation_id,
                    "platform": platform,
                    "artifact_ref": ref,
                    "artifact_revision": "r1",
                    "artifact_sha256": hashlib.sha256(content).hexdigest(),
                }
            )
            coverage.append(
                {
                    "platform": platform,
                    "status": "observed",
                    "observation_ids": [observation_id],
                    "reason": None,
                }
            )
            platforms.append(platform)
            observation_ids.append(observation_id)
        insight_id = "INS-setup-friction"
        package: dict[str, object] = {
            "project_id": "alpha-project",
            "project_profile_revision": "alpha-profile-v1",
            "package_version": "1.0",
            "package_id": "ARP-cross-platform",
            "package_revision": "r1",
            "decision_question": "Which setup-friction language should shape the next content experiment?",
            "requested_platforms": platforms,
            "research_status": "complete",
            "coverage": coverage,
            "observation_refs": refs,
            "insights": [
                {
                    "insight_id": insight_id,
                    "statement": "Setup friction appears repeatedly inside the bounded platform samples.",
                    "classification": insight_classification,
                    "supporting_observation_ids": observation_ids,
                    "supporting_platforms": platforms,
                    "confidence": confidence,
                    "decision_use": "Seed a falsifiable content-message experiment.",
                }
            ],
            "audience_segments": [
                {
                    "label": "People encountering setup friction",
                    "jobs": ["Finish initial setup"],
                    "pains": ["Unclear first step"],
                    "language": ["How do I start?"],
                    "evidence_insight_ids": [insight_id],
                }
            ],
            "content_patterns": [
                {
                    "pattern": "Concrete first-step walkthrough",
                    "platforms": platforms,
                    "evidence_insight_ids": [insight_id],
                    "caveat": "Direction only; no population-level prevalence claim.",
                }
            ],
            "recommendations": [
                {
                    "action": "Test a complete first-step tutorial.",
                    "evidence_insight_ids": [insight_id],
                    "success_signal": "Pre-registered qualified completion improves.",
                    "stop_condition": "Stop if the guardrail worsens or the sample is too small.",
                }
            ],
            "counterevidence": ["No population-representative comparison is available."],
            "unknowns": ["Platform ranking effects are not separable from audience interest."],
            "overall_confidence": confidence,
            "not_representative_acknowledged": True,
            "contains_personal_data": False,
            "created_at": "2026-08-31T13:00:00Z",
        }
        return package, artifacts

    def test_two_platform_official_package_passes(self) -> None:
        package, artifacts = self.package(
            [
                self.observation("OBS-youtube-main", "youtube"),
                self.observation("OBS-instagram-main", "instagram"),
            ]
        )

        report = audit_audience_research_package(self.profile, package, artifacts)

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["canonical_observation_count"], 2)

    def test_manual_evidence_adapter_prepares_exact_bytes_without_writing(self) -> None:
        observation, evidence = self.manual_observation()

        plan = prepare_manual_evidence_observation(
            self.profile, observation, evidence
        )

        self.assertEqual(plan.adapter_id, "manual-evidence-intake@1")
        self.assertEqual(
            plan.output_ref,
            "records/audience-observations/OBS-youtube-manual.json",
        )
        self.assertEqual(
            plan.output_sha256,
            hashlib.sha256(plan.output_content).hexdigest(),
        )
        self.assertEqual(json.loads(plan.output_content), observation)
        self.assertFalse((self.profile_root / "state").exists())

    def test_manual_evidence_adapter_rejects_hash_mismatch(self) -> None:
        observation, evidence = self.manual_observation()
        ref = next(iter(evidence))
        evidence[ref] = b"tampered\n"

        with self.assertRaisesRegex(AudienceResearchError, "hash mismatch"):
            prepare_manual_evidence_observation(
                self.profile, observation, evidence
            )

    def test_manual_evidence_adapter_rejects_cross_project_record(self) -> None:
        observation, evidence = self.manual_observation()
        observation["project_id"] = "other-project"

        with self.assertRaisesRegex(AudienceResearchError, "another project"):
            prepare_manual_evidence_observation(
                self.profile, observation, evidence
            )

    def test_manual_evidence_adapter_cannot_promote_unofficial_source(self) -> None:
        observation, evidence = self.manual_observation()
        observation["source_tier"] = "third_party_black_box"

        with self.assertRaisesRegex(AudienceResearchError, "cannot promote"):
            prepare_manual_evidence_observation(
                self.profile, observation, evidence
            )

    def test_bottom_up_source_is_retained_as_low_directional_signal(self) -> None:
        package, artifacts = self.package(
            [
                self.observation(
                    "OBS-tiktok-exploratory",
                    "tiktok",
                    access_method="third_party_scraping_service",
                    source_tier="third_party_black_box",
                    governance_use="idea_generation_only",
                    clearance="unknown",
                )
            ],
            confidence="low",
            insight_classification="directional",
        )

        report = audit_audience_research_package(self.profile, package, artifacts)

        self.assertEqual(report["status"], "warn")
        self.assertEqual(report["canonical_observation_count"], 0)
        self.assertEqual(report["discovery_only_observation_count"], 1)
        self.assertIn("discovery-only", report["warnings"][0])

    def test_bottom_up_source_cannot_claim_high_confidence(self) -> None:
        package, artifacts = self.package(
            [
                self.observation(
                    "OBS-x-exploratory",
                    "x",
                    access_method="session_cookie_capture",
                    source_tier="unofficial_session",
                    governance_use="idea_generation_only",
                    clearance="restricted",
                )
            ],
            confidence="high",
            insight_classification="platform_specific",
        )

        report = audit_audience_research_package(self.profile, package, artifacts)

        self.assertEqual(report["status"], "block")
        self.assertTrue(any("directional/low" in item for item in report["blockers"]))

    def test_exact_observation_hash_is_required(self) -> None:
        package, artifacts = self.package(
            [self.observation("OBS-youtube-hash", "youtube")],
            insight_classification="platform_specific",
        )
        ref = package["observation_refs"][0]  # type: ignore[index]
        ref["artifact_sha256"] = "0" * 64

        report = audit_audience_research_package(self.profile, package, artifacts)

        self.assertEqual(report["status"], "block")
        self.assertTrue(any("hash mismatch" in item for item in report["blockers"]))


if __name__ == "__main__":
    unittest.main()
