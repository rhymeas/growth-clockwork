from __future__ import annotations

import copy
from contextlib import closing
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from pipeline.agent_registry import (
    AgentRegistryError,
    RegistryArtifact,
    RouteContract,
    RouteNode,
    route_for_activation_decisions,
)
from pipeline.run_engine import _materialize_unblocked
from pipeline.validate_json_suites import SubsetValidator


WORKSPACE = Path(__file__).resolve().parents[2]
AGENTS = WORKSPACE / "agents"
CONTRACTS = WORKSPACE / "contracts"
PROFILE_PATHS = tuple(sorted((WORKSPACE / "projects").glob("*/project.json")))

EXPECTED_ROLE_IDS = {
    "analytics-experimentation",
    "audience-voc",
    "brand-creative",
    "community",
    "content-strategy",
    "demand-generation",
    "developer-relations",
    "editorial-production",
    "evidence-research",
    "governance-reviewer",
    "growth-coordinator",
    "growth-product-cro",
    "lifecycle-crm",
    "marketing-ops-revops",
    "paid-acquisition",
    "partnerships",
    "product-marketing-positioning",
    "search-ai-discovery",
    "social-media",
}
EXPECTED_PLATFORMS = {"tiktok", "youtube", "instagram", "pinterest", "x"}
PROHIBITED_ACTION_FLAGS = {
    "external_side_effects",
    "may_publish",
    "may_send",
    "may_spend",
    "may_contact",
    "may_contact_people",
    "may_modify_accounts",
    "may_mutate_accounts",
}
PROHIBITED_WRITE_SEGMENTS = {
    "accounts",
    "ads",
    "billing",
    "email",
    "outreach",
    "publish",
    "send",
    "social-accounts",
}


def _strict_json(path: Path) -> dict[str, object]:
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise AssertionError(f"duplicate JSON key {key!r}: {path}")
            value[key] = item
        return value

    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=lambda item: (_ for _ in ()).throw(
            AssertionError(f"non-JSON numeric constant {item}: {path}")
        ),
    )
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return value


def _expand_local_refs(value: object, root: dict[str, object]) -> object:
    """Expand local refs for the repository's dependency-free schema validator."""

    if isinstance(value, list):
        return [_expand_local_refs(item, root) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        if set(value) != {"$ref"}:
            raise AssertionError("conformance resolver accepts pure local $ref only")
        reference = value["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise AssertionError(f"unsupported schema reference: {reference!r}")
        target: object = root
        for raw_part in reference[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or part not in target:
                raise AssertionError(f"unresolved schema reference: {reference}")
            target = target[part]
        return _expand_local_refs(copy.deepcopy(target), root)
    # maxLength is checked explicitly below because the local validator rejects
    # schema keywords it does not implement.
    return {
        key: _expand_local_refs(item, root)
        for key, item in value.items()
        if key not in {"$defs", "maxLength"}
    }


def _validator(path: Path) -> SubsetValidator:
    raw = _strict_json(path)
    return SubsetValidator(_expand_local_refs(copy.deepcopy(raw), raw))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _route_contract(path: Path, value: dict[str, object]) -> RouteContract:
    content = path.read_bytes()
    nodes = tuple(
        RouteNode(
            node_id=node["node_id"],
            role_id=node["role_id"],
            depends_on=tuple(node["depends_on"]),
            objective=node["objective"],
            output_contract=node["output_contract"],
            deliverable_contract=node["deliverable_contract"],
            allowed_write_roots=tuple(node["allowed_write_roots"]),
            optional=node["optional"],
            run_when=node["run_when"],
        )
        for node in value["nodes"]
    )
    final = value["final_product"]
    terminal = value["terminal_review"]
    return RouteContract(
        route_id=value["route_id"],
        artifact=RegistryArtifact(
            ref=f"agents/routes/{path.name}",
            revision=value["route_id"],
            sha256=hashlib.sha256(content).hexdigest(),
            path=path,
            content=content,
            value=value,
        ),
        context_requirements=tuple(value["context_requirements"]),
        nodes=nodes,
        final_product_contract=final["contract"],
        final_product_node=final["producer_node"],
        governance_node=terminal["governance_node"],
        max_concurrent_workhorses=value["parallelism"][
            "max_concurrent_workhorses"
        ],
        only_independent_nodes=value["parallelism"]["only_independent_nodes"],
    )


def _walk_key_values(value: object):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from _walk_key_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_key_values(item)


class GrowthEngineConformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.role_validator = _validator(AGENTS / "schemas/role-spec.schema.json")
        cls.route_validator = _validator(AGENTS / "schemas/route-spec.schema.json")
        cls.role_paths = sorted((AGENTS / "roles").rglob("*.json"))
        cls.route_paths = sorted((AGENTS / "routes").glob("*.json"))
        cls.roles = {
            value["role_id"]: (path, value)
            for path in cls.role_paths
            for value in [_strict_json(path)]
        }
        cls.routes = {
            value["route_id"]: (path, value)
            for path in cls.route_paths
            for value in [_strict_json(path)]
        }
        cls.registry = _strict_json(AGENTS / "registry.json")
        cls.catalog = _strict_json(AGENTS / "catalog.json")
        cls.requirements = _strict_json(
            CONTRACTS / "deliverable-requirements.json"
        )["contracts"]

    def test_all_19_role_specs_are_valid_registered_and_hash_pinned(self) -> None:
        self.assertEqual(len(self.role_paths), 19)
        self.assertEqual(set(self.roles), EXPECTED_ROLE_IDS)
        self.assertEqual(len(self.roles), len(self.role_paths), "duplicate role_id")

        registered = {item["role_id"]: item for item in self.registry["roles"]}
        catalogued = {item["role_id"]: item for item in self.catalog["roles"]}
        self.assertEqual(set(registered), EXPECTED_ROLE_IDS)
        self.assertEqual(set(catalogued), EXPECTED_ROLE_IDS)

        for role_id, (path, role) in self.roles.items():
            with self.subTest(role=role_id):
                errors = self.role_validator.validate(role)
                self.assertEqual([item.to_dict() for item in errors], [])
                self.assertEqual(path.stem, role_id)
                self.assertLessEqual(len(role["display_name"]), 100)
                self.assertEqual(registered[role_id]["sha256"], _sha256(path))
                self.assertEqual(catalogued[role_id]["sha256"], _sha256(path))
                self.assertEqual(
                    (AGENTS / registered[role_id]["spec_ref"]).resolve(),
                    path.resolve(),
                )
                self.assertEqual(
                    (AGENTS / catalogued[role_id]["path"]).resolve(),
                    path.resolve(),
                )

    def test_all_route_files_are_valid_and_reach_every_role(self) -> None:
        self.assertGreaterEqual(len(self.routes), 6)
        self.assertEqual(len(self.routes), len(self.route_paths), "duplicate route_id")
        reached: set[str] = set()

        for route_id, (path, route) in self.routes.items():
            with self.subTest(route=route_id):
                errors = self.route_validator.validate(route)
                self.assertEqual([item.to_dict() for item in errors], [])
                self.assertEqual(path.stem, route_id)
                self.assertLessEqual(len(route["display_name"]), 100)
                for node in route["nodes"]:
                    role_id = node["role_id"]
                    self.assertIn(role_id, self.roles)
                    reached.add(role_id)

        self.assertEqual(reached, EXPECTED_ROLE_IDS)

        # Registered routes must be pinned correctly. Extra route files still count
        # for capability reachability while they are staged for profile registration.
        for entry in self.registry["routes"]:
            route_id = entry["route_id"]
            self.assertIn(route_id, self.routes)
            path, _ = self.routes[route_id]
            self.assertEqual((AGENTS / entry["spec_ref"]).resolve(), path.resolve())
            self.assertEqual(entry["sha256"], _sha256(path))

    def test_role_outputs_and_every_route_contract_have_professional_requirements(
        self,
    ) -> None:
        requirement_ids = set(self.requirements)
        declared_outputs = {
            output["contract"]
            for _, role in self.roles.values()
            for output in role["outputs"]
        }
        self.assertEqual(declared_outputs - requirement_ids, set())

        for route_id, (_, route) in self.routes.items():
            for node in route["nodes"]:
                with self.subTest(route=route_id, node=node["node_id"]):
                    role_outputs = {
                        output["contract"]
                        for output in self.roles[node["role_id"]][1]["outputs"]
                    }
                    self.assertIn(
                        node["deliverable_contract"],
                        role_outputs,
                        "route asks a role for a contract that role does not own",
                    )
                    self.assertIn(node["deliverable_contract"], requirement_ids)

            final = route["final_product"]
            for contract_id in [final["contract"], *final["supporting_contracts"]]:
                self.assertIn(contract_id, requirement_ids, route_id)
            self.assertIn(
                route["terminal_review"]["governance_contract"], requirement_ids
            )

        for contract_id, requirement in self.requirements.items():
            with self.subTest(contract=contract_id):
                self.assertTrue(requirement["required_payload_fields"])
                self.assertTrue(requirement["required_quality_checks"])

    def test_every_route_binds_final_producer_governance_and_human_review(self) -> None:
        for route_id, (_, route) in self.routes.items():
            with self.subTest(route=route_id):
                nodes = route["nodes"]
                by_id = {node["node_id"]: node for node in nodes}
                self.assertEqual(len(by_id), len(nodes))
                self.assertEqual(nodes[0]["node_id"], "coordinate")
                self.assertEqual(nodes[0]["role_id"], "growth-coordinator")
                self.assertFalse(nodes[0]["optional"])
                self.assertEqual(nodes[0]["depends_on"], [])

                for node in nodes:
                    self.assertTrue(set(node["depends_on"]).issubset(by_id))
                    self.assertEqual(node["output_contract"], "result-envelope@2")

                visiting: set[str] = set()
                visited: set[str] = set()

                def visit(node_id: str) -> None:
                    self.assertNotIn(node_id, visiting, f"cycle in {route_id}")
                    if node_id in visited:
                        return
                    visiting.add(node_id)
                    for dependency in by_id[node_id]["depends_on"]:
                        visit(dependency)
                    visiting.remove(node_id)
                    visited.add(node_id)

                for node_id in by_id:
                    visit(node_id)

                final = route["final_product"]
                producer = by_id[final["producer_node"]]
                self.assertFalse(producer["optional"])
                self.assertNotEqual(producer["role_id"], "governance-reviewer")
                self.assertEqual(producer["deliverable_contract"], final["contract"])

                terminal = route["terminal_review"]
                governance = by_id[terminal["governance_node"]]
                self.assertFalse(governance["optional"])
                self.assertEqual(governance["role_id"], "governance-reviewer")
                self.assertEqual(
                    governance["deliverable_contract"], "governance-verdict@1"
                )
                self.assertIn(final["producer_node"], governance["depends_on"])
                self.assertFalse(
                    any(
                        terminal["governance_node"] in node["depends_on"]
                        for node in nodes
                    ),
                    "final Governance must be terminal",
                )
                self.assertEqual(terminal["required_governance_result"], "pass")
                self.assertEqual(terminal["review_item_contract"], "outbox-review-item@1")
                self.assertEqual(terminal["terminal_state"], "review_ready")
                self.assertEqual(
                    set(terminal["human_actions"]), {"approve", "decline", "note"}
                )
                self.assertFalse(terminal["external_side_effects"])
                self.assertFalse(terminal["may_publish"])

    def test_optional_activation_and_max_concurrency_are_executable_controls(
        self,
    ) -> None:
        contracts: dict[str, RouteContract] = {}
        for route_id, (path, route) in self.routes.items():
            contract = _route_contract(path, route)
            contracts[route_id] = contract
            optional = [node for node in route["nodes"] if node["optional"]]
            for node in optional:
                self.assertNotEqual(node["run_when"].strip().casefold(), "always")
            decisions = [
                {
                    "step_id": node["node_id"],
                    "decision": "skip",
                    "reason": "Conformance fixture excludes this optional specialist.",
                }
                for node in optional
            ]
            active = route_for_activation_decisions(
                contract,
                {"step_activation": decisions},
                require_step_activation=True,
            )
            active_ids = {node.node_id for node in active.nodes}
            self.assertTrue(all(not node.optional for node in active.nodes))
            self.assertIn(active.final_product_node, active_ids)
            self.assertIn(active.governance_node, active_ids)
            for node in active.nodes:
                self.assertTrue(set(node.depends_on).issubset(active_ids))
            self.assertGreaterEqual(contract.max_concurrent_workhorses, 1)
            self.assertLessEqual(contract.max_concurrent_workhorses, 8)

            if optional:
                with self.assertRaisesRegex(
                    AgentRegistryError, "missing optional step activation"
                ):
                    route_for_activation_decisions(
                        contract,
                        {"step_activation": decisions[:-1]},
                        require_step_activation=True,
                    )

        # Runtime proof: with one of two slots occupied, the launch route emits
        # exactly one of two ready independent tasks. At the cap it emits none.
        launch = contracts["launch-readiness"]
        launch = route_for_activation_decisions(
            launch,
            {
                "step_activation": [
                    {
                        "step_id": node.node_id,
                        "decision": "skip",
                        "reason": "Conformance fixture excludes optional work.",
                    }
                    for node in launch.nodes
                    if node.optional
                ]
            },
            require_step_activation=True,
        )
        pointer = {
            "artifact_ref": "records/conformance/pointer.json",
            "artifact_revision": "r1",
            "artifact_sha256": "0" * 64,
        }
        manifest = {
            "run_id": "RUN-CONFORMANCE-CAP",
            "objective": "Synthetic cap proof.",
            "coordinator_plan": pointer,
            "project_context": pointer,
            "route_contract": pointer,
            "tasks": [
                {
                    "task_id": "EX-990-T001",
                    "step_id": "coordinate",
                    "status": "completed",
                },
                {
                    "task_id": "EX-990-T002",
                    "step_id": "launch-evidence",
                    "status": "queued",
                },
            ],
        }
        profile = {
            "project_id": "example-project",
            "profile_revision": "example-project-profile-v2",
            "task_id_pattern": r"^EX-[0-9]+(?:-T[0-9]{3,})?$",
        }
        plan = {"work_item_id": "EX-990", "objective": "Synthetic cap proof."}
        with patch("pipeline.run_engine._role_pointer", return_value=pointer), patch(
            "pipeline.run_engine._dependency_inputs", return_value=[]
        ), patch("pipeline.run_engine._validate_instance", return_value=None):
            _, entries = _materialize_unblocked(
                WORKSPACE,
                PROFILE_PATHS[0],
                profile,
                object(),
                launch,
                plan,
                manifest,
                "2026-08-31T00:00:00Z",
                {},
            )
        self.assertEqual([item["step_id"] for item in entries], ["product-readiness"])

        at_cap = copy.deepcopy(manifest)
        at_cap["tasks"].append(
            {
                "task_id": "EX-990-T003",
                "step_id": "product-readiness",
                "status": "running",
            }
        )
        writes, entries = _materialize_unblocked(
            WORKSPACE,
            PROFILE_PATHS[0],
            profile,
            object(),
            launch,
            plan,
            at_cap,
            "2026-08-31T00:00:00Z",
            {},
        )
        self.assertEqual((writes, entries), ([], []))

    def test_project_neutral_schemas_are_identical_across_profiles(self) -> None:
        canonical: dict[str, Path] = {}
        for path in sorted((CONTRACTS / "schemas").glob("*.json")):
            schema_id = _strict_json(path)["$id"]
            self.assertNotIn(schema_id, canonical)
            canonical[schema_id] = path
        self.assertGreaterEqual(len(canonical), 14)

        # A public package ships Example only; check every profile present.
        self.assertGreaterEqual(len(PROFILE_PATHS), 1)
        registries: list[tuple[Path, dict[str, object]]] = []
        for profile_path in PROFILE_PATHS:
            project_root = profile_path.parent
            registry = _strict_json(project_root / "schema-registry.json")["schemas"]
            registries.append((project_root, registry))
            for schema_id, source in canonical.items():
                with self.subTest(profile=project_root.name, schema=schema_id):
                    self.assertIn(schema_id, registry)
                    entry = registry[schema_id]
                    installed = project_root / entry["path"]
                    self.assertEqual(installed.read_bytes(), source.read_bytes())
                    self.assertEqual(entry["sha256"], _sha256(installed))

            profile = _strict_json(profile_path)
            binding = profile["professional_contracts"]
            for name in ("schema", "requirements"):
                pointer_value = binding[name]
                bound = WORKSPACE / pointer_value["artifact_ref"]
                self.assertEqual(pointer_value["artifact_sha256"], _sha256(bound))
            for root in ("memory", "code-intelligence", "evidence/platform"):
                self.assertIn(root, profile["writer"]["allowed_roots"])
                self.assertIn(root, profile["writer"]["append_only_roots"])

        shared = set.intersection(*(set(registry) for _, registry in registries))
        baseline_root, baseline_registry = registries[0]
        for project_root, registry in registries[1:]:
            for schema_id in shared:
                left = baseline_root / baseline_registry[schema_id]["path"]
                right = project_root / registry[schema_id]["path"]
                self.assertEqual(left.read_bytes(), right.read_bytes(), schema_id)
                self.assertEqual(
                    baseline_registry[schema_id]["sha256"],
                    registry[schema_id]["sha256"],
                    schema_id,
                )

    def test_memory_code_graph_audience_and_operating_modules_are_bound(self) -> None:
        expected = {
            "pipeline.project_memory": (
                "project-memory-record@1",
                (
                    "validate_memory_record",
                    "append_memory_record",
                    "query_project_memory",
                    "default_memory_index_path",
                    "rebuild_memory_index",
                    "query_project_memory_fts",
                ),
            ),
            "pipeline.code_graph": (
                "code-graph-index@1",
                ("assess_code_graph_index",),
            ),
            "pipeline.audience_research": (
                "platform-observation@1",
                ("validate_platform_observation", "audit_audience_research_package"),
            ),
            "pipeline.research_adapters": (
                None,
                ("load_research_adapter_registry", "plan_research_adapters"),
            ),
            "pipeline.operating_contract": (
                "growth-operating-contract@1",
                ("validate_operating_contract", "enforce_run_limits"),
            ),
            "pipeline.learning_loop": (
                None,
                ("close_learning_loop",),
            ),
        }
        for module_name, (schema_id, functions) in expected.items():
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                if module_name == "pipeline.audience_research":
                    self.assertEqual(module.OBSERVATION_SCHEMA_ID, schema_id)
                    self.assertEqual(
                        module.PACKAGE_SCHEMA_ID, "audience-research-package@1"
                    )
                elif module_name == "pipeline.research_adapters":
                    self.assertEqual(module.PLATFORMS, EXPECTED_PLATFORMS)
                elif module_name == "pipeline.learning_loop":
                    self.assertEqual(module.LOOP_VERSION, "1.0")
                    self.assertEqual(
                        module.SOURCE_KINDS,
                        {"release-outcome", "outcome-or-metric"},
                    )
                else:
                    self.assertEqual(module.SCHEMA_ID, schema_id)
                for function in functions:
                    self.assertTrue(callable(getattr(module, function)))

        operating = _strict_json(
            CONTRACTS / "schemas/growth-operating-contract.schema.json"
        )
        required = set(operating["required"])
        self.assertTrue(
            {
                "outcome_owner",
                "growth_stage",
                "decision_question",
                "success_signal",
                "guardrails",
                "stop_conditions",
                "resource_limits",
                "authority",
                "data_policy",
                "proof_requirements",
                "learning_contract",
            }.issubset(required)
        )

        memory = _strict_json(
            CONTRACTS / "schemas/project-memory-record.schema.json"
        )
        self.assertIn("exploratory", memory["properties"]["evidence_grade"]["enum"])
        code_graph = _strict_json(
            CONTRACTS / "schemas/code-graph-index.schema.json"
        )
        self.assertEqual(code_graph["properties"]["purpose"]["const"], "code_navigation_only")
        self.assertFalse(code_graph["properties"]["external_upload"]["const"])
        self.assertFalse(code_graph["properties"]["contains_secrets"]["const"])

    def test_memory_fts_cache_is_project_scoped_rebuildable_and_fail_closed(
        self,
    ) -> None:
        memory = importlib.import_module("pipeline.project_memory")
        default_paths: set[Path] = set()
        self.assertTrue(PROFILE_PATHS)

        for profile_path in PROFILE_PATHS:
            profile = _strict_json(profile_path)
            default_path = memory.default_memory_index_path(profile_path)
            default_paths.add(default_path)
            self.assertEqual(default_path.name, "memory-fts5.sqlite3")
            self.assertEqual(default_path.parent.name, profile["profile_revision"])
            self.assertEqual(default_path.parent.parent.name, profile["project_id"])
            self.assertIn(".growth-clockwork-cache", default_path.parts)

            with tempfile.TemporaryDirectory(prefix="growth-conformance-fts-") as root:
                index_path = Path(root) / "memory.sqlite3"
                receipt = memory.rebuild_memory_index(
                    profile_path, index_path=index_path
                )
                result = memory.query_project_memory_fts(
                    profile_path,
                    "conformance",
                    index_path=index_path,
                )

                self.assertFalse(receipt["canonical"])
                self.assertTrue(receipt["rebuildable"])
                self.assertEqual(receipt["project_id"], profile["project_id"])
                self.assertEqual(
                    receipt["project_profile_revision"], profile["profile_revision"]
                )
                self.assertRegex(receipt["source_revision"], r"^[0-9a-f]{64}$")
                self.assertFalse(result["canonical"])
                self.assertEqual(
                    result["source_revision"], receipt["source_revision"]
                )

                # sqlite3.Connection's context manager commits or rolls back;
                # it does not close the connection.
                with closing(sqlite3.connect(index_path)) as connection:
                    connection.execute(
                        "UPDATE metadata SET value = ? WHERE key = 'source_revision'",
                        ("0" * 64,),
                    )
                    connection.commit()
                with self.assertRaisesRegex(memory.ProjectMemoryError, "stale"):
                    memory.query_project_memory_fts(
                        profile_path,
                        "conformance",
                        index_path=index_path,
                    )

        self.assertEqual(len(default_paths), len(PROFILE_PATHS))

    def test_learning_loop_closes_two_internal_outputs_in_one_transaction(
        self,
    ) -> None:
        # Reuse the dedicated integration fixture as a transitive conformance
        # proof: it exercises the real Root Writer, validates both artifacts,
        # checks a two-write receipt, and verifies idempotent replay.
        learning_tests = importlib.import_module("pipeline.tests.test_learning_loop")
        case = learning_tests.LearningLoopTests(
            "test_exact_outcome_closes_memory_and_next_decision_in_one_transaction"
        )
        result = unittest.TestResult()
        case.run(result)
        details = [*result.failures, *result.errors]
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(result.skipped, [])
        self.assertEqual(details, [], details)

    def test_synthetic_schema_contract_suite_has_current_50_case_receipt(self) -> None:
        synthetic_profiles = [
            path
            for path in PROFILE_PATHS
            if _strict_json(path).get("product_motion") == "synthetic-fixture"
        ]
        self.assertEqual(len(synthetic_profiles), 1)
        fixture_root = synthetic_profiles[0].parent / "fixtures/schema-contracts"
        suite = _strict_json(fixture_root / "suite.json")
        report = _strict_json(fixture_root / "report.json")
        suite_ids = {case["id"] for case in suite["cases"]}
        report_ids = {case["id"] for case in report["cases"]}

        self.assertEqual(len(suite["cases"]), 50)
        self.assertEqual(len(suite_ids), 50)
        self.assertEqual(report_ids, suite_ids)
        self.assertEqual(
            report["summary"], {"total": 50, "passed": 50, "failed": 0}
        )

    def test_all_five_platforms_are_explicit_and_exploratory_is_noncanonical(
        self,
    ) -> None:
        observation = _strict_json(
            CONTRACTS / "schemas/platform-observation.schema.json"
        )
        audience = _strict_json(
            CONTRACTS / "schemas/audience-research-package.schema.json"
        )
        self.assertEqual(
            set(observation["properties"]["platform"]["enum"]), EXPECTED_PLATFORMS
        )
        self.assertEqual(
            set(audience["properties"]["requested_platforms"]["items"]["enum"]),
            EXPECTED_PLATFORMS,
        )

        # Private research-catalog assertions live beside their excluded inputs
        # in research/tests. Core assertions below use the shipped contracts.
        adapter_registry_path = CONTRACTS / "research-adapter-registry.json"
        adapter_registry = _strict_json(adapter_registry_path)
        invariants = adapter_registry["invariants"]
        self.assertTrue(invariants["read_only_collection"])
        self.assertTrue(invariants["no_publish_send_spend_or_account_mutation"])
        self.assertTrue(invariants["unofficial_sources_are_discovery_only"])
        adapters = {
            item["adapter_id"]: item for item in adapter_registry["adapters"]
        }
        represented = {
            platform
            for adapter in adapters.values()
            for platform in adapter["platforms"]
        }
        self.assertEqual(represented, EXPECTED_PLATFORMS)
        implemented_acquisition = {
            adapter_id
            for adapter_id, adapter in adapters.items()
            if adapter["kind"] == "acquisition"
            and adapter["implementation_status"] == "implemented"
        }
        self.assertEqual(implemented_acquisition, {"manual-evidence-intake"})
        self.assertTrue(adapters["manual-evidence-intake"]["default_enabled"])
        contract_only_ids = {
            "youtube-data-api",
            "instagram-owner-insights",
            "pinterest-owner-analytics",
            "tiktok-authorized-read",
            "x-api-read",
        }
        self.assertEqual(
            {
                adapter_id
                for adapter_id in contract_only_ids
                if adapters[adapter_id]["implementation_status"] == "contract_only"
            },
            contract_only_ids,
        )
        for adapter_id, adapter in adapters.items():
            with self.subTest(adapter=adapter_id):
                self.assertFalse(adapter["may_mutate_external_state"])
                if adapter_id != "manual-evidence-intake":
                    self.assertFalse(adapter["default_enabled"])
                if "unofficial_modelled" in adapter["access_classes"]:
                    self.assertEqual(adapter["implementation_status"], "reference_only")
                    self.assertFalse(adapter["canonical_output"])
                    self.assertEqual(
                        adapter["allowed_governance_uses"], ["idea_generation_only"]
                    )

        adapter_digest = _sha256(adapter_registry_path)
        research_adapters = importlib.import_module("pipeline.research_adapters")
        official_access = {
            "tiktok": "official_public",
            "youtube": "official_public",
            "instagram": "owner_authorized",
            "pinterest": "owner_authorized",
            "x": "official_public",
        }
        for profile_path in PROFILE_PATHS:
            profile = _strict_json(profile_path)
            pointer = profile["research_adapters"]
            self.assertEqual(
                pointer["artifact_ref"], "contracts/research-adapter-registry.json"
            )
            self.assertEqual(
                pointer["artifact_revision"], adapter_registry["registry_revision"]
            )
            self.assertEqual(pointer["artifact_sha256"], adapter_digest)
            loaded = research_adapters.load_research_adapter_registry(
                WORKSPACE, profile_path
            )
            self.assertEqual(loaded.sha256, adapter_digest)
            for platform in EXPECTED_PLATFORMS:
                manual = research_adapters.plan_research_adapters(
                    WORKSPACE,
                    profile_path,
                    platform=platform,
                    access_class="manual_evidence",
                )
                self.assertTrue(manual["dispatchable"])
                self.assertFalse(manual["executed"])
                self.assertEqual(
                    [item["adapter_id"] for item in manual["candidates"]],
                    ["manual-evidence-intake"],
                )

                official = research_adapters.plan_research_adapters(
                    WORKSPACE,
                    profile_path,
                    platform=platform,
                    access_class=official_access[platform],
                    require_implemented=False,
                )
                self.assertTrue(official["candidates"])
                self.assertFalse(official["dispatchable"])
                self.assertTrue(
                    all(
                        item["implementation_status"] == "contract_only"
                        for item in official["candidates"]
                        if item["kind"] == "acquisition"
                    )
                )

    def test_no_role_or_route_can_publish_spend_contact_or_mutate_accounts(self) -> None:
        self.assertNotIn("publisher", EXPECTED_ROLE_IDS)
        for role_id, (_, role) in self.roles.items():
            authority = role["authority"]
            with self.subTest(role=role_id):
                for key in (
                    "external_side_effects",
                    "may_publish",
                    "may_send",
                    "may_spend",
                    "may_modify_accounts",
                ):
                    self.assertFalse(authority[key])
                self.assertFalse(authority["direct_persistence"])

        for route_id, (_, route) in self.routes.items():
            with self.subTest(route=route_id):
                for key, value in _walk_key_values(route):
                    if key in PROHIBITED_ACTION_FLAGS:
                        self.assertIs(value, False, f"{route_id}.{key}")
                for node in route["nodes"]:
                    for root in node["allowed_write_roots"]:
                        segments = set(Path(root).parts)
                        self.assertFalse(
                            segments & PROHIBITED_WRITE_SEGMENTS,
                            f"external action-like write root in {route_id}/{node['node_id']}: {root}",
                        )

        operating = _strict_json(
            CONTRACTS / "schemas/growth-operating-contract.schema.json"
        )
        authority_schema = operating["properties"]["authority"]["properties"]
        for key in (
            "external_side_effects",
            "may_publish",
            "may_spend",
            "may_contact_people",
        ):
            self.assertIs(authority_schema[key]["const"], False)


if __name__ == "__main__":
    unittest.main()
