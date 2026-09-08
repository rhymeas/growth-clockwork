#!/usr/bin/env python3
"""Run portable JSON contract suites without third-party dependencies.

The runner is product-agnostic. A required project profile supplies:

1. the selected project identity and profile revision;
2. a schema registry with relative paths and pinned SHA-256 hashes; and
3. a suite manifest containing fixtures and expected outcomes.

Suite, registry, schema, and fixture paths are confined to that profile directory.
Every fixture must carry the selected project identity and profile revision.

This is a deliberately small JSON Schema 2020-12 subset validator. It rejects
unknown schema keywords instead of silently accepting semantics it does not
implement. Exit codes: 0 = suite passed, 1 = expectation mismatch, 2 = invalid
configuration, unsupported schema, or unreadable input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


ANNOTATION_KEYWORDS = {
    "$id",
    "$schema",
    "description",
    "title",
}

ASSERTION_KEYWORDS = {
    "additionalProperties",
    "allOf",
    "const",
    "contains",
    "else",
    "enum",
    "format",
    "if",
    "items",
    "maxContains",
    "maximum",
    "maxItems",
    "minContains",
    "minimum",
    "minItems",
    "minLength",
    "oneOf",
    "pattern",
    "properties",
    "required",
    "then",
    "type",
    "uniqueItems",
}

SUPPORTED_KEYWORDS = ANNOTATION_KEYWORDS | ASSERTION_KEYWORDS
SUPPORTED_SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"
SUPPORTED_JSON_TYPES = {
    "array",
    "boolean",
    "integer",
    "null",
    "number",
    "object",
    "string",
}


class SuiteConfigurationError(Exception):
    """Raised when a suite, registry, schema, or fixture is malformed."""


@dataclass(frozen=True)
class ValidationError:
    instance_path: str
    schema_path: str
    rule: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "instance_path": self.instance_path,
            "schema_path": self.schema_path,
            "rule": self.rule,
            "message": self.message,
        }


@dataclass(frozen=True)
class RegisteredSchema:
    """A hash-verified schema selected through one project profile."""

    project_id: str
    project_profile_revision: str
    schema_id: str
    schema_path: Path
    sha256: str
    validator: "SubsetValidator"


def _parse_json_bytes(content: bytes, path: Path) -> Any:
    def reject_non_json_number(value: str) -> None:
        raise ValueError(f"non-JSON numeric constant {value}")

    try:
        return json.loads(
            content.decode("utf-8"),
            parse_constant=reject_non_json_number,
        )
    except json.JSONDecodeError as exc:
        raise SuiteConfigurationError(
            f"invalid JSON in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    except (UnicodeError, ValueError) as exc:
        raise SuiteConfigurationError(f"invalid or unreadable JSON in {path}: {exc}") from exc


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise SuiteConfigurationError(f"file not found: {path}") from exc
    except OSError as exc:
        raise SuiteConfigurationError(f"cannot read {path}: {exc}") from exc


def _load_json(path: Path) -> Any:
    return _parse_json_bytes(_read_bytes(path), path)


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _json_equal(left[key], right[key]) for key in left
        )
    return left == right


def _instance_type_matches(instance: Any, expected: str) -> bool:
    if expected == "null":
        return instance is None
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "integer":
        return (
            isinstance(instance, int)
            and not isinstance(instance, bool)
        ) or (
            isinstance(instance, float)
            and instance.is_integer()
        )
    raise SuiteConfigurationError(f"unsupported JSON Schema type: {expected!r}")


def _is_rfc3339_datetime(value: str) -> bool:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _property_path(base: str, name: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return f"{base}.{name}"
    return f"{base}[{json.dumps(name, ensure_ascii=False)}]"


def _schema_path(base: str, name: str | int) -> str:
    return f"{base}/{name}"


def _is_nonnegative_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _check_schema_keywords(schema: Any, path: str = "#") -> None:
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise SuiteConfigurationError(f"schema node at {path} must be object or boolean")
    if not all(isinstance(keyword, str) for keyword in schema):
        raise SuiteConfigurationError(f"schema keyword at {path} must be a string")

    unknown = sorted(set(schema) - SUPPORTED_KEYWORDS)
    if unknown:
        raise SuiteConfigurationError(
            f"unsupported schema keyword(s) at {path}: {', '.join(unknown)}"
        )

    if "$schema" in schema and schema["$schema"] != SUPPORTED_SCHEMA_URI:
        raise SuiteConfigurationError(
            f"unsupported $schema at {path}: {schema['$schema']!r}"
        )
    for keyword in ("$id", "title", "description"):
        if keyword in schema and not isinstance(schema[keyword], str):
            raise SuiteConfigurationError(f"{keyword} at {path} must be a string")
    if "$id" in schema and not schema["$id"]:
        raise SuiteConfigurationError(f"$id at {path} must not be empty")

    if "type" in schema:
        declared_type = schema["type"]
        declared_types = (
            declared_type if isinstance(declared_type, list) else [declared_type]
        )
        if not declared_types or not all(
            isinstance(item, str) for item in declared_types
        ):
            raise SuiteConfigurationError(
                f"type at {path} must be a string or non-empty string array"
            )
        if len(set(declared_types)) != len(declared_types):
            raise SuiteConfigurationError(f"type at {path} contains duplicates")
        unsupported_types = sorted(set(declared_types) - SUPPORTED_JSON_TYPES)
        if unsupported_types:
            raise SuiteConfigurationError(
                f"unsupported JSON Schema type(s) at {path}: {', '.join(unsupported_types)}"
            )

    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, list) or not all(
            isinstance(name, str) for name in required
        ):
            raise SuiteConfigurationError(
                f"required at {path} must be a string array"
            )
        if len(set(required)) != len(required):
            raise SuiteConfigurationError(f"required at {path} contains duplicates")

    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise SuiteConfigurationError(f"properties at {path} must be an object")
    if not all(isinstance(name, str) for name in properties):
        raise SuiteConfigurationError(f"property name at {path} must be a string")
    for name, subschema in properties.items():
        _check_schema_keywords(subschema, _schema_path(_schema_path(path, "properties"), name))

    if "additionalProperties" in schema and not isinstance(
        schema["additionalProperties"], bool
    ):
        raise SuiteConfigurationError(
            f"additionalProperties at {path} must be boolean in this supported subset"
        )

    if "enum" in schema:
        choices = schema["enum"]
        if not isinstance(choices, list) or not choices:
            raise SuiteConfigurationError(f"enum at {path} must be a non-empty array")
        for index, choice in enumerate(choices):
            if any(_json_equal(choice, prior) for prior in choices[:index]):
                raise SuiteConfigurationError(f"enum at {path} contains duplicates")

    for keyword in (
        "minLength",
        "minItems",
        "maxItems",
        "minContains",
        "maxContains",
    ):
        if keyword in schema and not _is_nonnegative_integer(schema[keyword]):
            raise SuiteConfigurationError(
                f"{keyword} at {path} must be a non-negative integer"
            )
    if (
        "minItems" in schema
        and "maxItems" in schema
        and schema["minItems"] > schema["maxItems"]
    ):
        raise SuiteConfigurationError(f"minItems exceeds maxItems at {path}")
    if ("minContains" in schema or "maxContains" in schema) and "contains" not in schema:
        raise SuiteConfigurationError(
            f"minContains/maxContains at {path} require contains in this supported subset"
        )
    if (
        "minContains" in schema
        and "maxContains" in schema
        and schema["minContains"] > schema["maxContains"]
    ):
        raise SuiteConfigurationError(f"minContains exceeds maxContains at {path}")

    for keyword in ("minimum", "maximum"):
        if keyword in schema and not _is_finite_number(schema[keyword]):
            raise SuiteConfigurationError(
                f"{keyword} at {path} must be a finite number"
            )
    if (
        "minimum" in schema
        and "maximum" in schema
        and schema["minimum"] > schema["maximum"]
    ):
        raise SuiteConfigurationError(f"minimum exceeds maximum at {path}")

    if "uniqueItems" in schema and not isinstance(schema["uniqueItems"], bool):
        raise SuiteConfigurationError(f"uniqueItems at {path} must be boolean")

    if "pattern" in schema:
        pattern = schema["pattern"]
        if not isinstance(pattern, str):
            raise SuiteConfigurationError(f"pattern at {path} must be a string")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise SuiteConfigurationError(
                f"invalid regular expression at {path}/pattern: {exc}"
            ) from exc

    if "format" in schema and schema["format"] != "date-time":
        raise SuiteConfigurationError(
            f"unsupported format at {path}: {schema['format']!r}"
        )

    for keyword in ("allOf", "oneOf"):
        if keyword in schema:
            children = schema[keyword]
            if not isinstance(children, list) or not children:
                raise SuiteConfigurationError(
                    f"{keyword} at {path} must be a non-empty array"
                )
            for index, subschema in enumerate(children):
                _check_schema_keywords(
                    subschema, _schema_path(_schema_path(path, keyword), index)
                )

    if ("then" in schema or "else" in schema) and "if" not in schema:
        raise SuiteConfigurationError(
            f"then/else at {path} require if in this supported subset"
        )
    for keyword in ("items", "contains", "if", "then", "else"):
        if keyword in schema:
            _check_schema_keywords(schema[keyword], _schema_path(path, keyword))


class SubsetValidator:
    """Validate the JSON Schema subset used by the registered contract suites."""

    def __init__(self, schema: Any):
        _check_schema_keywords(schema)
        self.schema = schema

    def validate(self, instance: Any) -> list[ValidationError]:
        return self._validate(instance, self.schema, "$", "#")

    def _error(
        self,
        instance_path: str,
        schema_path: str,
        rule: str,
        message: str,
    ) -> ValidationError:
        return ValidationError(instance_path, schema_path, rule, message)

    def _validate(
        self,
        instance: Any,
        schema: Any,
        instance_path: str,
        schema_path: str,
    ) -> list[ValidationError]:
        if schema is True:
            return []
        if schema is False:
            return [
                self._error(instance_path, schema_path, "falseSchema", "value is forbidden")
            ]

        errors: list[ValidationError] = []

        if "type" in schema:
            expected = schema["type"]
            allowed = expected if isinstance(expected, list) else [expected]
            if not all(isinstance(value, str) for value in allowed):
                raise SuiteConfigurationError(f"type at {schema_path} must be string or array")
            if not any(_instance_type_matches(instance, value) for value in allowed):
                errors.append(
                    self._error(
                        instance_path,
                        _schema_path(schema_path, "type"),
                        "type",
                        f"expected {' or '.join(allowed)}",
                    )
                )

        if "const" in schema and not _json_equal(instance, schema["const"]):
            errors.append(
                self._error(
                    instance_path,
                    _schema_path(schema_path, "const"),
                    "const",
                    f"expected constant {json.dumps(schema['const'], ensure_ascii=False)}",
                )
            )

        if "enum" in schema:
            choices = schema["enum"]
            if not isinstance(choices, list) or not choices:
                raise SuiteConfigurationError(f"enum at {schema_path} must be a non-empty array")
            if not any(_json_equal(instance, choice) for choice in choices):
                errors.append(
                    self._error(
                        instance_path,
                        _schema_path(schema_path, "enum"),
                        "enum",
                        "value is not in the allowed set",
                    )
                )

        if isinstance(instance, dict):
            required = schema.get("required", [])
            if not isinstance(required, list) or not all(
                isinstance(name, str) for name in required
            ):
                raise SuiteConfigurationError(f"required at {schema_path} must be a string array")
            for name in required:
                if name not in instance:
                    errors.append(
                        self._error(
                            instance_path,
                            _schema_path(schema_path, "required"),
                            "required",
                            f"missing required property {name!r}",
                        )
                    )

            properties = schema.get("properties", {})
            for name, subschema in properties.items():
                if name in instance:
                    errors.extend(
                        self._validate(
                            instance[name],
                            subschema,
                            _property_path(instance_path, name),
                            _schema_path(_schema_path(schema_path, "properties"), name),
                        )
                    )

            if schema.get("additionalProperties") is False:
                for name in sorted(set(instance) - set(properties)):
                    errors.append(
                        self._error(
                            _property_path(instance_path, name),
                            _schema_path(schema_path, "additionalProperties"),
                            "additionalProperties",
                            f"unexpected property {name!r}",
                        )
                    )

        if isinstance(instance, list):
            if "minItems" in schema and len(instance) < schema["minItems"]:
                errors.append(
                    self._error(
                        instance_path,
                        _schema_path(schema_path, "minItems"),
                        "minItems",
                        f"expected at least {schema['minItems']} item(s)",
                    )
                )
            if "maxItems" in schema and len(instance) > schema["maxItems"]:
                errors.append(
                    self._error(
                        instance_path,
                        _schema_path(schema_path, "maxItems"),
                        "maxItems",
                        f"expected at most {schema['maxItems']} item(s)",
                    )
                )
            if schema.get("uniqueItems") is True:
                for index, value in enumerate(instance):
                    if any(_json_equal(value, prior) for prior in instance[:index]):
                        errors.append(
                            self._error(
                                instance_path,
                                _schema_path(schema_path, "uniqueItems"),
                                "uniqueItems",
                                f"item at index {index} is duplicated",
                            )
                        )
                        break
            if "items" in schema:
                for index, value in enumerate(instance):
                    errors.extend(
                        self._validate(
                            value,
                            schema["items"],
                            f"{instance_path}[{index}]",
                            _schema_path(schema_path, "items"),
                        )
                    )
            if "contains" in schema:
                matches = sum(
                    not self._validate(
                        value,
                        schema["contains"],
                        f"{instance_path}[{index}]",
                        _schema_path(schema_path, "contains"),
                    )
                    for index, value in enumerate(instance)
                )
                minimum = schema.get("minContains", 1)
                maximum = schema.get("maxContains")
                if matches < minimum or (maximum is not None and matches > maximum):
                    bound = f"between {minimum} and {maximum}" if maximum is not None else f"at least {minimum}"
                    errors.append(
                        self._error(
                            instance_path,
                            _schema_path(schema_path, "contains"),
                            "contains",
                            f"expected {bound} matching item(s), found {matches}",
                        )
                    )

        if isinstance(instance, str):
            if "minLength" in schema and len(instance) < schema["minLength"]:
                errors.append(
                    self._error(
                        instance_path,
                        _schema_path(schema_path, "minLength"),
                        "minLength",
                        f"expected at least {schema['minLength']} character(s)",
                    )
                )
            if "pattern" in schema:
                try:
                    matched = re.search(schema["pattern"], instance) is not None
                except re.error as exc:
                    raise SuiteConfigurationError(
                        f"invalid regular expression at {schema_path}/pattern: {exc}"
                    ) from exc
                if not matched:
                    errors.append(
                        self._error(
                            instance_path,
                            _schema_path(schema_path, "pattern"),
                            "pattern",
                            f"value does not match {schema['pattern']!r}",
                        )
                    )
            if schema.get("format") == "date-time" and not _is_rfc3339_datetime(instance):
                errors.append(
                    self._error(
                        instance_path,
                        _schema_path(schema_path, "format"),
                        "format",
                        "expected an RFC 3339 date-time with an explicit timezone",
                    )
                )
            elif "format" in schema and schema["format"] != "date-time":
                raise SuiteConfigurationError(
                    f"unsupported format at {schema_path}: {schema['format']!r}"
                )

        if isinstance(instance, (int, float)) and not isinstance(instance, bool):
            if "minimum" in schema and instance < schema["minimum"]:
                errors.append(
                    self._error(
                        instance_path,
                        _schema_path(schema_path, "minimum"),
                        "minimum",
                        f"expected value >= {schema['minimum']}",
                    )
                )
            if "maximum" in schema and instance > schema["maximum"]:
                errors.append(
                    self._error(
                        instance_path,
                        _schema_path(schema_path, "maximum"),
                        "maximum",
                        f"expected value <= {schema['maximum']}",
                    )
                )

        if "allOf" in schema:
            for index, subschema in enumerate(schema["allOf"]):
                errors.extend(
                    self._validate(
                        instance,
                        subschema,
                        instance_path,
                        _schema_path(_schema_path(schema_path, "allOf"), index),
                    )
                )

        if "oneOf" in schema:
            branch_errors = [
                self._validate(
                    instance,
                    subschema,
                    instance_path,
                    _schema_path(_schema_path(schema_path, "oneOf"), index),
                )
                for index, subschema in enumerate(schema["oneOf"])
            ]
            matches = sum(not branch for branch in branch_errors)
            if matches != 1:
                errors.append(
                    self._error(
                        instance_path,
                        _schema_path(schema_path, "oneOf"),
                        "oneOf",
                        f"expected exactly one matching branch, found {matches}",
                    )
                )

        if "if" in schema:
            condition_errors = self._validate(
                instance,
                schema["if"],
                instance_path,
                _schema_path(schema_path, "if"),
            )
            selected = "then" if not condition_errors else "else"
            if selected in schema:
                errors.extend(
                    self._validate(
                        instance,
                        schema[selected],
                        instance_path,
                        _schema_path(schema_path, selected),
                    )
                )

        return errors


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SuiteConfigurationError(f"{label} must be a JSON object")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SuiteConfigurationError(f"{label} must be a non-empty string")
    return value


def _resolve_within(profile_root: Path, candidate: Path, label: str) -> Path:
    resolved = candidate.resolve()
    try:
        resolved.relative_to(profile_root)
    except ValueError as exc:
        raise SuiteConfigurationError(
            f"{label} escapes project profile directory: {resolved}"
        ) from exc
    return resolved


def _load_project_profile(
    project_config_path: Path,
) -> tuple[Path, dict[str, Any], Path]:
    config_path = project_config_path.resolve()
    profile_root = config_path.parent
    profile = _require_mapping(_load_json(config_path), "project profile")
    project_id = _require_string(profile.get("project_id"), "project.project_id")
    profile_revision = _require_string(
        profile.get("profile_revision"), "project.profile_revision"
    )
    registry_ref = _require_string(
        profile.get("schema_registry"), "project.schema_registry"
    )
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", project_id):
        raise SuiteConfigurationError("project.project_id is not a portable identifier")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", profile_revision):
        raise SuiteConfigurationError(
            "project.profile_revision is not a portable identifier"
        )
    registry_ref_path = Path(registry_ref)
    if registry_ref_path.is_absolute():
        raise SuiteConfigurationError("project.schema_registry must be relative")
    registry_path = _resolve_within(
        profile_root,
        profile_root / registry_ref_path,
        "project schema registry",
    )
    return profile_root, profile, registry_path


def _load_registry(
    suite: dict[str, Any],
    suite_path: Path,
    profile_root: Path,
    expected_registry_path: Path,
) -> tuple[Path, dict[str, Any]]:
    registry_ref = _require_string(suite.get("registry"), "suite.registry")
    registry_ref_path = Path(registry_ref)
    if registry_ref_path.is_absolute():
        raise SuiteConfigurationError("suite.registry must be relative")
    registry_path = _resolve_within(
        profile_root,
        suite_path.parent / registry_ref_path,
        "suite registry",
    )
    if registry_path != expected_registry_path:
        raise SuiteConfigurationError(
            "suite.registry does not match project.schema_registry"
        )
    registry = _require_mapping(_load_json(registry_path), "registry")
    if registry.get("registry_version") != "1.0":
        raise SuiteConfigurationError("registry.registry_version must equal '1.0'")
    schemas = _require_mapping(registry.get("schemas"), "registry.schemas")
    if not schemas:
        raise SuiteConfigurationError("registry.schemas must not be empty")
    return registry_path, schemas


def load_registered_schema(
    project_config_path: Path, schema_id: str
) -> RegisteredSchema:
    """Load one project-registered schema after path, ID, and hash verification."""

    profile_root, profile, registry_path = _load_project_profile(project_config_path)
    registry = _require_mapping(_load_json(registry_path), "registry")
    if registry.get("registry_version") != "1.0":
        raise SuiteConfigurationError("registry.registry_version must equal '1.0'")
    schemas = _require_mapping(registry.get("schemas"), "registry.schemas")
    entry = _require_mapping(
        schemas.get(schema_id), f"registry.schemas[{schema_id!r}]"
    )
    path_ref = _require_string(entry.get("path"), f"registry schema {schema_id}.path")
    path_ref_path = Path(path_ref)
    if path_ref_path.is_absolute():
        raise SuiteConfigurationError(
            f"registry schema {schema_id}.path must be relative"
        )
    expected_hash = _require_string(
        entry.get("sha256"), f"registry schema {schema_id}.sha256"
    )
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise SuiteConfigurationError(f"invalid SHA-256 for schema {schema_id}")
    schema_path = _resolve_within(
        profile_root,
        registry_path.parent / path_ref_path,
        f"schema {schema_id}",
    )
    schema_bytes = _read_bytes(schema_path)
    actual_hash = hashlib.sha256(schema_bytes).hexdigest()
    if actual_hash != expected_hash:
        raise SuiteConfigurationError(
            f"schema hash mismatch for {schema_id}: "
            f"expected {expected_hash}, got {actual_hash}"
        )
    schema = _parse_json_bytes(schema_bytes, schema_path)
    declared_id = schema.get("$id") if isinstance(schema, dict) else None
    if declared_id != schema_id:
        raise SuiteConfigurationError(
            f"schema registry id {schema_id!r} does not match $id {declared_id!r}"
        )
    return RegisteredSchema(
        project_id=profile["project_id"],
        project_profile_revision=profile["profile_revision"],
        schema_id=schema_id,
        schema_path=schema_path,
        sha256=actual_hash,
        validator=SubsetValidator(schema),
    )


def validate_registered_instance(
    project_config_path: Path,
    schema_id: str,
    instance: Any,
    *,
    require_project_identity: bool = True,
) -> list[ValidationError]:
    """Validate one runtime object against a selected registered contract."""

    registered = load_registered_schema(project_config_path, schema_id)
    if require_project_identity:
        if not isinstance(instance, dict):
            raise SuiteConfigurationError(
                "runtime instance must be an object with project identity"
            )
        if instance.get("project_id") != registered.project_id:
            raise SuiteConfigurationError(
                "runtime instance project_id does not match the selected profile"
            )
        if (
            instance.get("project_profile_revision")
            != registered.project_profile_revision
        ):
            raise SuiteConfigurationError(
                "runtime instance project_profile_revision does not match the selected profile"
            )
    return registered.validator.validate(instance)


def _expected_failure_seen(errors: Iterable[ValidationError], expected: dict[str, Any]) -> bool:
    rule = _require_string(expected.get("rule"), "case.expected_failure.rule")
    instance_path = expected.get("instance_path")
    if instance_path is not None and not isinstance(instance_path, str):
        raise SuiteConfigurationError("case.expected_failure.instance_path must be a string")
    return any(
        error.rule == rule
        and (instance_path is None or error.instance_path == instance_path)
        for error in errors
    )


def run_suite(
    suite_path: Path, project_config_path: Path
) -> tuple[dict[str, Any], bool]:
    profile_root, profile, expected_registry_path = _load_project_profile(
        project_config_path
    )
    suite_path = _resolve_within(profile_root, suite_path, "suite")
    suite = _require_mapping(_load_json(suite_path), "suite")
    if suite.get("suite_version") != "1.0":
        raise SuiteConfigurationError("suite.suite_version must equal '1.0'")
    suite_name = _require_string(suite.get("name"), "suite.name")
    registry_path, registry = _load_registry(
        suite,
        suite_path,
        profile_root,
        expected_registry_path,
    )

    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        raise SuiteConfigurationError("suite.cases must be a non-empty array")

    validators: dict[str, SubsetValidator] = {}
    schema_report: list[dict[str, str]] = []
    for schema_id in sorted({_require_string(case.get("schema"), "case.schema") for case in cases if isinstance(case, dict)}):
        entry = _require_mapping(registry.get(schema_id), f"registry.schemas[{schema_id!r}]")
        path_ref = _require_string(entry.get("path"), f"registry schema {schema_id}.path")
        path_ref_path = Path(path_ref)
        if path_ref_path.is_absolute():
            raise SuiteConfigurationError(
                f"registry schema {schema_id}.path must be relative"
            )
        expected_hash = _require_string(
            entry.get("sha256"), f"registry schema {schema_id}.sha256"
        )
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise SuiteConfigurationError(f"invalid SHA-256 for schema {schema_id}")
        schema_path = _resolve_within(
            profile_root,
            registry_path.parent / path_ref_path,
            f"schema {schema_id}",
        )
        schema_bytes = _read_bytes(schema_path)
        actual_hash = hashlib.sha256(schema_bytes).hexdigest()
        if actual_hash != expected_hash:
            raise SuiteConfigurationError(
                f"schema hash mismatch for {schema_id}: expected {expected_hash}, got {actual_hash}"
            )
        schema = _parse_json_bytes(schema_bytes, schema_path)
        declared_id = schema.get("$id") if isinstance(schema, dict) else None
        if declared_id != schema_id:
            raise SuiteConfigurationError(
                f"schema registry id {schema_id!r} does not match $id {declared_id!r}"
            )
        validators[schema_id] = SubsetValidator(schema)
        schema_report.append(
            {"id": schema_id, "sha256": actual_hash, "status": "verified"}
        )

    seen_case_ids: set[str] = set()
    case_report: list[dict[str, Any]] = []
    for index, raw_case in enumerate(cases):
        case = _require_mapping(raw_case, f"suite.cases[{index}]")
        case_id = _require_string(case.get("id"), f"suite.cases[{index}].id")
        if case_id in seen_case_ids:
            raise SuiteConfigurationError(f"duplicate case id: {case_id}")
        seen_case_ids.add(case_id)
        schema_id = _require_string(case.get("schema"), f"case {case_id}.schema")
        fixture_ref = _require_string(case.get("fixture"), f"case {case_id}.fixture")
        fixture_ref_path = Path(fixture_ref)
        if fixture_ref_path.is_absolute():
            raise SuiteConfigurationError(
                f"case {case_id}.fixture must be relative"
            )
        expectation = _require_string(case.get("expect"), f"case {case_id}.expect")
        if expectation not in {"valid", "invalid"}:
            raise SuiteConfigurationError(
                f"case {case_id}.expect must be 'valid' or 'invalid'"
            )
        fixture_path = _resolve_within(
            profile_root,
            suite_path.parent / fixture_ref_path,
            f"fixture for case {case_id}",
        )
        instance = _load_json(fixture_path)
        if not isinstance(instance, dict):
            raise SuiteConfigurationError(
                f"fixture for case {case_id} must be an object with project identity"
            )
        if instance.get("project_id") != profile["project_id"]:
            raise SuiteConfigurationError(
                f"fixture for case {case_id} has project_id that does not match the selected profile"
            )
        if instance.get("project_profile_revision") != profile["profile_revision"]:
            raise SuiteConfigurationError(
                f"fixture for case {case_id} has project_profile_revision that does not match the selected profile"
            )
        errors = validators[schema_id].validate(instance)
        actual = "valid" if not errors else "invalid"
        passed = actual == expectation

        expected_failure = case.get("expected_failure")
        expected_failure_seen = None
        if expectation == "invalid":
            if not isinstance(expected_failure, dict):
                raise SuiteConfigurationError(
                    f"negative case {case_id} requires expected_failure"
                )
            expected_failure_seen = _expected_failure_seen(errors, expected_failure)
            passed = passed and expected_failure_seen
        elif expected_failure is not None:
            raise SuiteConfigurationError(
                f"positive case {case_id} must not define expected_failure"
            )

        result: dict[str, Any] = {
            "id": case_id,
            "schema": schema_id,
            "fixture": fixture_ref,
            "expect": expectation,
            "actual": actual,
            "passed": passed,
            "errors": [error.to_dict() for error in errors],
        }
        if expected_failure_seen is not None:
            result["expected_failure_seen"] = expected_failure_seen
        case_report.append(result)

    passed_count = sum(case["passed"] for case in case_report)
    report = {
        "report_version": "1.0",
        "project": {
            "project_id": profile["project_id"],
            "project_profile_revision": profile["profile_revision"],
        },
        "suite": suite_name,
        "schemas": schema_report,
        "summary": {
            "total": len(case_report),
            "passed": passed_count,
            "failed": len(case_report) - passed_count,
        },
        "cases": case_report,
    }
    return report, passed_count == len(case_report)


def _render_report(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-config",
        required=True,
        type=Path,
        help="path to the selected projects/<profile>/project.json",
    )
    parser.add_argument("suite", type=Path, help="path to a project suite manifest")
    args = parser.parse_args(argv)

    try:
        report, passed = run_suite(
            args.suite.resolve(),
            args.project_config.resolve(),
        )
    except SuiteConfigurationError as exc:
        error_report = {
            "report_version": "1.0",
            "status": "configuration_error",
            "error": str(exc),
        }
        sys.stdout.write(_render_report(error_report))
        return 2

    rendered = _render_report(report)
    sys.stdout.write(rendered)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
