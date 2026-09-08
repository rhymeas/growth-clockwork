from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from pipeline.validate_json_suites import (
    SubsetValidator,
    SuiteConfigurationError,
    load_registered_schema,
    run_suite,
    validate_registered_instance,
)


class ProjectIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.profile = self.base / "projects" / "alpha"
        self.fixtures = self.profile / "fixtures"
        self.schemas = self.profile / "schemas"
        self.fixtures.mkdir(parents=True)
        self.schemas.mkdir(parents=True)

        self.schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "example-envelope@1",
            "type": "object",
            "required": ["project_id", "project_profile_revision", "value"],
            "properties": {
                "project_id": {"type": "string"},
                "project_profile_revision": {"type": "string"},
                "value": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        }
        self.schema_path = self.schemas / "example.schema.json"
        self._write_json(self.schema_path, self.schema)
        self.registry_path = self.profile / "schema-registry.json"
        self._write_json(
            self.registry_path,
            {
                "registry_version": "1.0",
                "schemas": {
                    "example-envelope@1": {
                        "path": "schemas/example.schema.json",
                        "sha256": self._hash(self.schema_path),
                    }
                },
            },
        )
        self.project_config = self.profile / "project.json"
        self._write_json(
            self.project_config,
            {
                "profile_version": "1.0",
                "profile_revision": "alpha-profile-v1",
                "project_id": "alpha-project",
                "schema_registry": "schema-registry.json",
            },
        )
        self.fixture_path = self.fixtures / "valid.json"
        self._write_json(
            self.fixture_path,
            {
                "project_id": "alpha-project",
                "project_profile_revision": "alpha-profile-v1",
                "value": "valid",
            },
        )
        self.suite_path = self.fixtures / "suite.json"
        self._write_json(
            self.suite_path,
            {
                "suite_version": "1.0",
                "name": "alpha-suite",
                "registry": "../schema-registry.json",
                "cases": [
                    {
                        "id": "valid",
                        "schema": "example-envelope@1",
                        "fixture": "valid.json",
                        "expect": "valid",
                    }
                ],
            },
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_profile_bound_suite_passes(self) -> None:
        report, passed = run_suite(self.suite_path, self.project_config)

        self.assertTrue(passed)
        self.assertEqual(
            report["project"],
            {
                "project_id": "alpha-project",
                "project_profile_revision": "alpha-profile-v1",
            },
        )

    def test_suite_outside_profile_is_rejected(self) -> None:
        outside_suite = self.base / "outside-suite.json"
        self._write_json(outside_suite, json.loads(self.suite_path.read_text()))

        with self.assertRaisesRegex(SuiteConfigurationError, "suite escapes"):
            run_suite(outside_suite, self.project_config)

    def test_suite_cannot_select_another_registry(self) -> None:
        alternate = self.profile / "alternate-registry.json"
        self._write_json(alternate, json.loads(self.registry_path.read_text()))
        suite = json.loads(self.suite_path.read_text())
        suite["registry"] = "../alternate-registry.json"
        self._write_json(self.suite_path, suite)

        with self.assertRaisesRegex(
            SuiteConfigurationError, "does not match project.schema_registry"
        ):
            run_suite(self.suite_path, self.project_config)

    def test_fixture_from_another_project_is_rejected(self) -> None:
        fixture = json.loads(self.fixture_path.read_text())
        fixture["project_id"] = "beta-project"
        self._write_json(self.fixture_path, fixture)

        with self.assertRaisesRegex(SuiteConfigurationError, "project_id"):
            run_suite(self.suite_path, self.project_config)

    def test_registry_cannot_reference_schema_outside_profile(self) -> None:
        outside_schema = self.base / "outside.schema.json"
        self._write_json(outside_schema, self.schema)
        registry = json.loads(self.registry_path.read_text())
        registry["schemas"]["example-envelope@1"]["path"] = "../../outside.schema.json"
        registry["schemas"]["example-envelope@1"]["sha256"] = self._hash(
            outside_schema
        )
        self._write_json(self.registry_path, registry)

        with self.assertRaisesRegex(SuiteConfigurationError, "schema .* escapes"):
            run_suite(self.suite_path, self.project_config)

    def test_non_json_numeric_constant_is_rejected_cleanly(self) -> None:
        self.fixture_path.write_text(
            '{"project_id":"alpha-project",'
            '"project_profile_revision":"alpha-profile-v1",'
            '"value":NaN}\n',
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            SuiteConfigurationError, "non-JSON numeric constant NaN"
        ):
            run_suite(self.suite_path, self.project_config)

    def test_runtime_instance_uses_the_selected_registered_schema(self) -> None:
        registered = load_registered_schema(
            self.project_config, "example-envelope@1"
        )
        errors = validate_registered_instance(
            self.project_config,
            "example-envelope@1",
            {
                "project_id": "alpha-project",
                "project_profile_revision": "alpha-profile-v1",
                "value": "runtime-valid",
            },
        )

        self.assertEqual(registered.project_id, "alpha-project")
        self.assertEqual(registered.schema_path, self.schema_path.resolve())
        self.assertEqual(errors, [])

    def test_runtime_instance_cannot_cross_project_identity(self) -> None:
        with self.assertRaisesRegex(
            SuiteConfigurationError, "project_id does not match"
        ):
            validate_registered_instance(
                self.project_config,
                "example-envelope@1",
                {
                    "project_id": "beta-project",
                    "project_profile_revision": "alpha-profile-v1",
                    "value": "cross-project",
                },
            )

    def test_runtime_instance_cannot_use_unregistered_schema(self) -> None:
        with self.assertRaisesRegex(
            SuiteConfigurationError, r"registry.schemas\['missing-envelope@1'\]"
        ):
            validate_registered_instance(
                self.project_config,
                "missing-envelope@1",
                {
                    "project_id": "alpha-project",
                    "project_profile_revision": "alpha-profile-v1",
                    "value": "unknown-schema",
                },
            )


class SchemaShapeTests(unittest.TestCase):
    def test_malformed_or_unsupported_keyword_forms_fail_closed(self) -> None:
        cases = [
            (
                "additionalProperties subschema",
                {"additionalProperties": {"type": "string"}},
                "additionalProperties .* must be boolean",
            ),
            (
                "non-string type",
                {"type": 7},
                "type .* must be a string",
            ),
            (
                "unsupported schema draft",
                {"$schema": "http://json-schema.org/draft-07/schema#"},
                "unsupported \\$schema",
            ),
            (
                "unsupported type name",
                {"type": "decimal"},
                "unsupported JSON Schema type",
            ),
            (
                "non-integer string bound",
                {"minLength": "1"},
                "minLength .* non-negative integer",
            ),
            (
                "boolean item bound",
                {"minItems": True},
                "minItems .* non-negative integer",
            ),
            (
                "reversed item bounds",
                {"minItems": 2, "maxItems": 1},
                "minItems exceeds maxItems",
            ),
            (
                "orphaned contains bound",
                {"minContains": 1},
                "minContains/maxContains .* require contains",
            ),
            (
                "reversed contains bounds",
                {"contains": True, "minContains": 2, "maxContains": 1},
                "minContains exceeds maxContains",
            ),
            (
                "malformed pattern",
                {"pattern": "["},
                "invalid regular expression",
            ),
            (
                "empty applicator",
                {"allOf": []},
                "allOf .* non-empty array",
            ),
            (
                "duplicate required name",
                {"required": ["value", "value"]},
                "required .* contains duplicates",
            ),
            (
                "non-boolean uniqueness",
                {"uniqueItems": "true"},
                "uniqueItems .* must be boolean",
            ),
            (
                "unsupported format",
                {"format": "email"},
                "unsupported format",
            ),
            (
                "conditional branch without condition",
                {"then": {"type": "string"}},
                "then/else .* require if",
            ),
            (
                "non-finite numeric bound",
                {"minimum": float("nan")},
                "minimum .* finite number",
            ),
            (
                "non-string schema keyword",
                {1: "not-json"},
                "schema keyword .* string",
            ),
        ]

        for name, schema, message in cases:
            with self.subTest(name=name):
                with self.assertRaisesRegex(SuiteConfigurationError, message):
                    SubsetValidator(schema)

    def test_supported_boolean_additional_properties_is_applied(self) -> None:
        closed = SubsetValidator(
            {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "additionalProperties": False,
            }
        )
        open_schema = SubsetValidator({"type": "object", "additionalProperties": True})

        self.assertEqual(open_schema.validate({"extra": 1}), [])
        self.assertEqual(
            [error.rule for error in closed.validate({"value": "ok", "extra": 1})],
            ["additionalProperties"],
        )


if __name__ == "__main__":
    unittest.main()
