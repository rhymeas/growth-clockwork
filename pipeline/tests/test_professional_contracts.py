from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from pipeline.professional_contracts import (
    ProfessionalContractError,
    load_professional_contracts,
    validate_deliverable_bytes,
)


WORKSPACE = Path(__file__).resolve().parents[2]
SCHEMA_REVISION = "professional-deliverable-v1"
REQUIREMENTS_REVISION = "professional-deliverable-requirements-v1"
TASK_SHA256 = "1" * 64


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProfessionalContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name) / "workspace"
        (self.workspace / "contracts").mkdir(parents=True)
        (self.workspace / "projects/example/state/evidence").mkdir(parents=True)
        shutil.copy2(
            WORKSPACE / "contracts/professional-deliverable.schema.json",
            self.workspace / "contracts/professional-deliverable.schema.json",
        )
        shutil.copy2(
            WORKSPACE / "contracts/deliverable-requirements.json",
            self.workspace / "contracts/deliverable-requirements.json",
        )
        self.evidence_path = (
            self.workspace / "projects/example/state/evidence/source-fixture.json"
        )
        self.evidence_path.write_text(
            '{"fixture":"source","scope":"synthetic"}\n', encoding="utf-8"
        )
        self.evidence_ref = {
            "artifact_ref": "evidence/source-fixture.json",
            "artifact_revision": "source-r1",
            "artifact_sha256": sha256(self.evidence_path),
        }
        self.profile_path = self.workspace / "projects/example/project.json"
        self._write_profile()
        self.bundle = load_professional_contracts(
            self.workspace, self.profile_path
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _profile(self) -> dict[str, object]:
        schema_path = self.workspace / "contracts/professional-deliverable.schema.json"
        requirements_path = self.workspace / "contracts/deliverable-requirements.json"
        return {
            "project_id": "example-project",
            "profile_revision": "example-profile-r1",
            "unrelated_profile_field": {"is_accepted": True},
            "writer": {"state_root": "projects/example/state"},
            "professional_contracts": {
                "schema": {
                    "artifact_ref": "contracts/professional-deliverable.schema.json",
                    "artifact_revision": SCHEMA_REVISION,
                    "artifact_sha256": sha256(schema_path),
                },
                "requirements": {
                    "artifact_ref": "contracts/deliverable-requirements.json",
                    "artifact_revision": REQUIREMENTS_REVISION,
                    "artifact_sha256": sha256(requirements_path),
                },
            },
        }

    def _write_profile(self, profile: dict[str, object] | None = None) -> None:
        self._write_json(self.profile_path, profile or self._profile())

    @staticmethod
    def _payload_value(field_type: str) -> object:
        return {
            "string": "professionally defined",
            "array": [{"status": "defined"}],
            "object": {"status": "defined"},
            "boolean": False,
            "integer": 1,
            "number": 1.0,
        }[field_type]

    def _deliverable(self, contract_id: str) -> dict[str, object]:
        requirement = self.bundle.contracts[contract_id]
        payload = {
            field: self._payload_value(field_type)
            for field, field_type in requirement.required_payload_fields.items()
        }
        payload["unicode_note"] = "Größe und café stay exact"
        checks = [
            {
                "check_id": check_id,
                "status": "pass",
                "evidence_refs": [copy.deepcopy(self.evidence_ref)],
            }
            for check_id in requirement.required_quality_checks
        ]
        return {
            "deliverable_version": "1.0",
            "project_id": "example-project",
            "project_profile_revision": "example-profile-r1",
            "run_id": "RUN-example-001",
            "task_id": "EX-1000-T001",
            "task_sha256": TASK_SHA256,
            "step_id": "professional-step",
            "role_id": "professional-role",
            "contract_id": contract_id,
            "artifact_revision": "artifact-r1",
            "evidence_refs": [copy.deepcopy(self.evidence_ref)],
            "unknowns": ["One explicitly bounded unknown"],
            "assumptions": ["One explicit assumption"],
            "confidence": "medium",
            "quality_checks": checks,
            "payload": payload,
            "created_at": "2026-08-31T12:00:00Z",
        }

    @staticmethod
    def _bytes(value: object) -> bytes:
        return (
            json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")

    def _validate(
        self, value: dict[str, object], *, expected_contract_id: str | None = None
    ):
        return validate_deliverable_bytes(
            self.bundle,
            self._bytes(value),
            expected_run_id="RUN-example-001",
            expected_task_id="EX-1000-T001",
            expected_task_sha256=TASK_SHA256,
            expected_step_id="professional-step",
            expected_role_id="professional-role",
            expected_contract_id=expected_contract_id or str(value["contract_id"]),
            expected_artifact_revision="artifact-r1",
        )

    def test_every_registered_professional_contract_accepts_a_complete_deliverable(
        self,
    ) -> None:
        self.assertEqual(
            set(self.bundle.contracts),
            {
                "adapter-specification@1",
                "audience-change-proposal@1",
                "audience-research-package@1",
                "channel-product@1",
                "community-program@1",
                "creative-package@1",
                "decision-record@1",
                "demand-plan@1",
                "developer-experience-package@1",
                "discovery-package@1",
                "evidence-packet@1",
                "experiment-charter@1",
                "funnel-diagnosis@1",
                "governance-verdict@1",
                "launch-readiness-package@1",
                "learning-record@1",
                "lesson-brief@1",
                "lifecycle-plan@1",
                "measurement-learning-package@1",
                "measurement-plan@1",
                "partner-fit-memo@1",
                "paused-paid-experiment@1",
                "positioning-package@1",
                "rollout-recommendation@1",
                "route-decision@1",
                "social-package@1",
                "source-map@1",
                "task-set@1",
                "validated-snapshot@1",
            },
        )
        for contract_id in self.bundle.contracts:
            with self.subTest(contract_id=contract_id):
                value = self._deliverable(contract_id)
                content = self._bytes(value)
                validated = validate_deliverable_bytes(
                    self.bundle,
                    content,
                    expected_run_id="RUN-example-001",
                    expected_task_id="EX-1000-T001",
                    expected_task_sha256=TASK_SHA256,
                    expected_step_id="professional-step",
                    expected_role_id="professional-role",
                    expected_contract_id=contract_id,
                    expected_artifact_revision="artifact-r1",
                )
                self.assertEqual(validated.content, content)
                self.assertEqual(
                    validated.sha256, hashlib.sha256(content).hexdigest()
                )
                self.assertEqual(validated.requirement.contract_id, contract_id)

    def test_checked_in_profiles_load_the_same_pinned_contract_catalog(self) -> None:
        expected_contracts = set(self.bundle.contracts)
        profiles = sorted((WORKSPACE / "projects").glob("*/project.json"))
        self.assertIn(WORKSPACE / "projects/example/project.json", profiles)
        for profile in profiles:
            with self.subTest(project_name=profile.parent.name):
                bundle = load_professional_contracts(
                    WORKSPACE, profile
                )
                self.assertEqual(set(bundle.contracts), expected_contracts)
                self.assertEqual(bundle.schema.revision, SCHEMA_REVISION)
                self.assertEqual(bundle.requirements.revision, REQUIREMENTS_REVISION)

    def test_requirements_cover_every_node_final_and_supporting_route_contract(
        self,
    ) -> None:
        route_contracts: set[str] = set()
        for path in (WORKSPACE / "agents/routes").glob("*.json"):
            route = json.loads(path.read_text(encoding="utf-8"))
            route_contracts.update(
                node["deliverable_contract"] for node in route["nodes"]
            )
            route_contracts.add(route["final_product"]["contract"])
            route_contracts.update(route["final_product"]["supporting_contracts"])

        self.assertTrue(route_contracts.issubset(self.bundle.contracts))

    def test_tampered_schema_or_requirements_bytes_are_rejected(self) -> None:
        for filename, error in (
            ("professional-deliverable.schema.json", "professional schema hash mismatch"),
            ("deliverable-requirements.json", "deliverable requirements hash mismatch"),
        ):
            with self.subTest(filename=filename):
                path = self.workspace / "contracts" / filename
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                try:
                    with self.assertRaisesRegex(ProfessionalContractError, error):
                        load_professional_contracts(self.workspace, self.profile_path)
                finally:
                    path.write_bytes(original)

    def test_requirements_revision_must_match_the_profile_pointer(self) -> None:
        profile = self._profile()
        profile["professional_contracts"]["requirements"][
            "artifact_revision"
        ] = "wrong-revision"
        self._write_profile(profile)

        with self.assertRaisesRegex(
            ProfessionalContractError, "revision does not match its pointer"
        ):
            load_professional_contracts(self.workspace, self.profile_path)

    def test_contract_pointer_traversal_and_symlink_are_rejected(self) -> None:
        profile = self._profile()
        profile["professional_contracts"]["requirements"][
            "artifact_ref"
        ] = "contracts/../contracts/deliverable-requirements.json"
        self._write_profile(profile)
        with self.assertRaisesRegex(ProfessionalContractError, "escapes"):
            load_professional_contracts(self.workspace, self.profile_path)

        link = self.workspace / "contracts/requirements-link.json"
        link.symlink_to(self.workspace / "contracts/deliverable-requirements.json")
        profile = self._profile()
        profile["professional_contracts"]["requirements"][
            "artifact_ref"
        ] = "contracts/requirements-link.json"
        self._write_profile(profile)
        with self.assertRaisesRegex(ProfessionalContractError, "symlink"):
            load_professional_contracts(self.workspace, self.profile_path)

    def test_non_utf8_duplicate_key_and_non_json_number_are_rejected(self) -> None:
        invalid_cases = (
            (b"\xff", "strict UTF-8 JSON"),
            (
                b'{"project_id":"example-project","project_id":"other"}',
                "duplicate object key",
            ),
            (b'{"value":NaN}', "non-JSON numeric constant"),
        )
        for content, error in invalid_cases:
            with self.subTest(error=error):
                with self.assertRaisesRegex(ProfessionalContractError, error):
                    validate_deliverable_bytes(
                        self.bundle,
                        content,
                        expected_run_id="RUN-example-001",
                        expected_task_id="EX-1000-T001",
                        expected_task_sha256=TASK_SHA256,
                        expected_step_id="professional-step",
                        expected_role_id="professional-role",
                        expected_contract_id="evidence-packet@1",
                        expected_artifact_revision="artifact-r1",
                    )

    def test_each_identity_dimension_is_bound_to_the_caller(self) -> None:
        base = self._deliverable("evidence-packet@1")
        mutations = {
            "project_id": "another-project",
            "project_profile_revision": "another-profile-r1",
            "run_id": "RUN-another-001",
            "task_id": "EX-1000-T002",
            "task_sha256": "2" * 64,
            "step_id": "another-step",
            "role_id": "another-role",
            "contract_id": "source-map@1",
            "artifact_revision": "artifact-r2",
        }
        for field, replacement in mutations.items():
            with self.subTest(field=field):
                value = copy.deepcopy(base)
                value[field] = replacement
                with self.assertRaisesRegex(ProfessionalContractError, field):
                    self._validate(value, expected_contract_id="evidence-packet@1")

    def test_missing_wrong_type_and_empty_required_payload_fields_are_rejected(
        self,
    ) -> None:
        base = self._deliverable("evidence-packet@1")
        cases = []
        missing = copy.deepcopy(base)
        del missing["payload"]["decision_question"]
        cases.append((missing, "missing required field 'decision_question'"))
        wrong_type = copy.deepcopy(base)
        wrong_type["payload"]["method"] = []
        cases.append((wrong_type, "field 'method' must be object"))
        empty = copy.deepcopy(base)
        empty["payload"]["method"] = {}
        cases.append((empty, "field 'method' is empty"))
        blank = copy.deepcopy(base)
        blank["payload"]["decision_question"] = "   "
        cases.append((blank, "field 'decision_question' is empty"))

        for value, error in cases:
            with self.subTest(error=error):
                with self.assertRaisesRegex(ProfessionalContractError, error):
                    self._validate(value)

    def test_missing_duplicate_and_non_pass_quality_checks_are_rejected(self) -> None:
        base = self._deliverable("evidence-packet@1")
        missing = copy.deepcopy(base)
        missing["quality_checks"] = missing["quality_checks"][1:]
        with self.assertRaisesRegex(
            ProfessionalContractError, "missing required quality checks"
        ):
            self._validate(missing)

        duplicate = copy.deepcopy(base)
        duplicate["quality_checks"].append(
            copy.deepcopy(duplicate["quality_checks"][0])
        )
        with self.assertRaisesRegex(ProfessionalContractError, "duplicate quality check"):
            self._validate(duplicate)

        failed = copy.deepcopy(base)
        failed["quality_checks"][0]["status"] = "not_run"
        with self.assertRaisesRegex(ProfessionalContractError, "is not pass"):
            self._validate(failed)

    def test_missing_tampered_and_undeclared_evidence_are_rejected(self) -> None:
        no_evidence = self._deliverable("evidence-packet@1")
        no_evidence["evidence_refs"] = []
        with self.assertRaisesRegex(ProfessionalContractError, "minItems"):
            self._validate(no_evidence)

        tampered = self._deliverable("evidence-packet@1")
        tampered["evidence_refs"][0]["artifact_sha256"] = "f" * 64
        with self.assertRaisesRegex(ProfessionalContractError, "hash mismatch"):
            self._validate(tampered)

        second_path = self.workspace / "projects/example/state/evidence/second.json"
        second_path.write_text('{"fixture":"second"}\n', encoding="utf-8")
        second_ref = {
            "artifact_ref": "evidence/second.json",
            "artifact_revision": "source-r1",
            "artifact_sha256": sha256(second_path),
        }
        undeclared = self._deliverable("evidence-packet@1")
        undeclared["quality_checks"][0]["evidence_refs"] = [second_ref]
        with self.assertRaisesRegex(ProfessionalContractError, "undeclared evidence"):
            self._validate(undeclared)

    def test_unregistered_contract_cannot_self_declare_validity(self) -> None:
        value = self._deliverable("evidence-packet@1")
        value["contract_id"] = "unknown-output@1"
        with self.assertRaisesRegex(
            ProfessionalContractError, "unregistered professional deliverable contract"
        ):
            self._validate(value, expected_contract_id="unknown-output@1")


if __name__ == "__main__":
    unittest.main()
