from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import unittest

from pipeline.validate_json_suites import SubsetValidator


WORKSPACE = Path(__file__).resolve().parents[2]
AGENTS = WORKSPACE / "agents"
NEW_ROUTE_IDS = {
    "audience-deep-research",
    "organic-audience-community",
    "demand-acquisition",
    "lifecycle-retention",
    "marketing-operations",
}
EXPECTED_SPECIALIST_REACHABILITY = {
    "community": "organic-audience-community",
    "developer-relations": "organic-audience-community",
    "social-media": "organic-audience-community",
    "demand-generation": "demand-acquisition",
    "paid-acquisition": "demand-acquisition",
    "partnerships": "demand-acquisition",
    "marketing-ops-revops": "marketing-operations",
}
EXPECTED_FINAL_PRODUCERS = {
    "organic-audience-community": (
        "organic-package",
        "product-marketing-positioning",
        "launch-readiness-package@1",
    ),
    "demand-acquisition": (
        "acquisition-package",
        "product-marketing-positioning",
        "launch-readiness-package@1",
    ),
    "lifecycle-retention": (
        "lifecycle-plan",
        "lifecycle-crm",
        "lifecycle-plan@1",
    ),
    "marketing-operations": (
        "operations-design",
        "marketing-ops-revops",
        "adapter-specification@1",
    ),
    "audience-deep-research": (
        "audience-synthesis",
        "audience-voc",
        "audience-change-proposal@1",
    ),
}


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _expand_local_refs(value: object, root: dict[str, object]) -> object:
    if isinstance(value, list):
        return [_expand_local_refs(item, root) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        if set(value) != {"$ref"}:
            raise AssertionError("test resolver supports pure local $ref objects only")
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
    return {
        key: _expand_local_refs(item, root)
        for key, item in value.items()
        if key not in {"$defs", "maxLength"}
    }


def _route_specs() -> dict[str, dict[str, object]]:
    routes: dict[str, dict[str, object]] = {}
    for path in sorted((AGENTS / "routes").glob("*.json")):
        route = _json(path)
        route_id = route.get("route_id")
        if not isinstance(route_id, str):
            raise AssertionError(f"route_id is missing: {path}")
        if route_id in routes:
            raise AssertionError(f"duplicate route_id: {route_id}")
        if path.stem != route_id:
            raise AssertionError(f"route filename does not match route_id: {path}")
        routes[route_id] = route
    return routes


class ModularSpecialistRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        raw_schema = _json(AGENTS / "schemas/route-spec.schema.json")
        expanded_schema = _expand_local_refs(copy.deepcopy(raw_schema), raw_schema)
        cls.validator = SubsetValidator(expanded_schema)
        raw_catalog_schema = _json(AGENTS / "schemas/catalog.schema.json")
        expanded_catalog_schema = _expand_local_refs(
            copy.deepcopy(raw_catalog_schema), raw_catalog_schema
        )
        cls.catalog_validator = SubsetValidator(expanded_catalog_schema)
        cls.registry = _json(AGENTS / "registry.json")
        cls.catalog = _json(AGENTS / "catalog.json")
        cls.requirements = _json(
            WORKSPACE / "contracts/deliverable-requirements.json"
        )["contracts"]
        cls.routes = _route_specs()
        cls.roles = {
            entry["role_id"]: _json(AGENTS / entry["spec_ref"])
            for entry in cls.registry["roles"]
        }
        cls.example_profile = _json(WORKSPACE / "projects/example/project.json")
        cls.non_example_profiles = [
            _json(path)
            for path in sorted((WORKSPACE / "projects").glob("*/project.json"))
            if path.parent.name != "example"
        ]

    def test_new_routes_validate_against_the_portable_route_schema(self) -> None:
        self.assertTrue(NEW_ROUTE_IDS.issubset(self.routes))
        for route_id in sorted(NEW_ROUTE_IDS):
            errors = self.validator.validate(self.routes[route_id])
            self.assertEqual(
                [error.to_dict() for error in errors],
                [],
                route_id,
            )
            self.assertLessEqual(len(self.routes[route_id]["display_name"]), 100)

    def test_new_routes_preserve_control_and_professional_contract_boundaries(self) -> None:
        known_roles = {entry["role_id"] for entry in self.registry["roles"]}
        known_deliverables = set(self.requirements)
        for route_id in sorted(NEW_ROUTE_IDS):
            route = self.routes[route_id]
            nodes = route["nodes"]
            by_id = {node["node_id"]: node for node in nodes}
            self.assertEqual(len(by_id), len(nodes), route_id)
            self.assertEqual(nodes[0]["node_id"], "coordinate", route_id)
            self.assertEqual(nodes[0]["role_id"], "growth-coordinator", route_id)
            self.assertFalse(nodes[0]["optional"], route_id)
            self.assertEqual(nodes[0]["depends_on"], [], route_id)

            for node in nodes:
                self.assertIn(node["role_id"], known_roles, route_id)
                self.assertIn(
                    node["deliverable_contract"], known_deliverables, route_id
                )
                self.assertTrue(
                    set(node["depends_on"]).issubset(by_id),
                    f"{route_id}/{node['node_id']}",
                )
                if node["optional"]:
                    self.assertNotEqual(node["run_when"].lower(), "always", route_id)
                    self.assertGreaterEqual(len(node["run_when"].strip()), 20, route_id)

            final = route["final_product"]
            producer = by_id[final["producer_node"]]
            expected_node, expected_role, expected_contract = (
                EXPECTED_FINAL_PRODUCERS[route_id]
            )
            self.assertEqual(final["producer_node"], expected_node, route_id)
            self.assertEqual(producer["role_id"], expected_role, route_id)
            self.assertEqual(final["contract"], expected_contract, route_id)
            self.assertFalse(producer["optional"], route_id)
            self.assertEqual(
                producer["deliverable_contract"], final["contract"], route_id
            )
            governance = by_id[route["terminal_review"]["governance_node"]]
            self.assertEqual(governance["role_id"], "governance-reviewer", route_id)
            self.assertFalse(governance["optional"], route_id)
            self.assertEqual(
                governance["deliverable_contract"], "governance-verdict@1", route_id
            )
            self.assertFalse(route["terminal_review"]["external_side_effects"])
            self.assertFalse(route["terminal_review"]["may_publish"])
            self.assertGreaterEqual(
                route["parallelism"]["max_concurrent_workhorses"], 1
            )
            self.assertLessEqual(
                route["parallelism"]["max_concurrent_workhorses"], 8
            )

            visiting: set[str] = set()
            visited: set[str] = set()

            def visit(step_id: str) -> None:
                self.assertNotIn(step_id, visiting, f"cycle in {route_id}")
                if step_id in visited:
                    return
                visiting.add(step_id)
                for dependency in by_id[step_id]["depends_on"]:
                    visit(dependency)
                visiting.remove(step_id)
                visited.add(step_id)

            for step_id in by_id:
                visit(step_id)
            self.assertIn(final["producer_node"], governance["depends_on"])

    def test_every_global_role_has_at_least_one_route_spec(self) -> None:
        known_roles = {entry["role_id"] for entry in self.registry["roles"]}
        reached = {
            node["role_id"]
            for route in self.routes.values()
            for node in route["nodes"]
        }
        self.assertEqual(known_roles - reached, set())

        for role_id, route_id in EXPECTED_SPECIALIST_REACHABILITY.items():
            self.assertIn(
                role_id,
                {node["role_id"] for node in self.routes[route_id]["nodes"]},
            )

        for role_id in {
            "social-media",
            "community",
            "developer-relations",
            "demand-generation",
            "paid-acquisition",
            "partnerships",
        }:
            route_id = EXPECTED_SPECIALIST_REACHABILITY[role_id]
            node = next(
                node
                for node in self.routes[route_id]["nodes"]
                if node["role_id"] == role_id
            )
            self.assertTrue(node["optional"], f"{route_id}/{role_id}")

    def test_route_contracts_are_declared_by_roles_and_professionally_defined(
        self,
    ) -> None:
        requirement_ids = set(self.requirements)
        for role_id, role in self.roles.items():
            for output in role["outputs"]:
                self.assertIn(
                    output["contract"],
                    requirement_ids,
                    f"{role_id}/{output['output_id']}",
                )

        for route_id, route in self.routes.items():
            for node in route["nodes"]:
                role_contracts = {
                    output["contract"]
                    for output in self.roles[node["role_id"]]["outputs"]
                }
                self.assertIn(
                    node["deliverable_contract"],
                    role_contracts,
                    f"{route_id}/{node['node_id']}",
                )
                self.assertIn(node["deliverable_contract"], requirement_ids)

    def test_new_routes_are_registered_hash_pinned_and_catalogued_exactly(
        self,
    ) -> None:
        self.assertEqual(
            [
                error.to_dict()
                for error in self.catalog_validator.validate(self.catalog)
            ],
            [],
        )
        registered = {
            entry["route_id"]: entry for entry in self.registry["routes"]
        }
        catalogued = {
            entry["route_id"]: entry for entry in self.catalog["routes"]
        }
        self.assertTrue(NEW_ROUTE_IDS.issubset(registered))
        self.assertTrue(NEW_ROUTE_IDS.issubset(catalogued))

        for route_id in sorted(NEW_ROUTE_IDS):
            route = self.routes[route_id]
            route_path = AGENTS / "routes" / f"{route_id}.json"
            expected_hash = _sha256(route_path)
            self.assertEqual(
                registered[route_id],
                {
                    "route_id": route_id,
                    "spec_ref": f"routes/{route_id}.json",
                    "sha256": expected_hash,
                },
            )

            expected_steps = [
                {
                    ("step_id" if key == "node_id" else key): value
                    for key, value in node.items()
                }
                for node in route["nodes"]
            ]
            catalog_entry = catalogued[route_id]
            self.assertEqual(catalog_entry["path"], f"routes/{route_id}.json")
            self.assertEqual(catalog_entry["sha256"], expected_hash)
            self.assertEqual(
                catalog_entry["required_context"], route["context_requirements"]
            )
            self.assertEqual(catalog_entry["steps"], expected_steps)
            self.assertEqual(
                catalog_entry["final_product_contract"],
                route["final_product"]["contract"],
            )
            self.assertEqual(
                catalog_entry["governance_step_id"],
                route["terminal_review"]["governance_node"],
            )
            self.assertEqual(
                catalog_entry["terminal_state"],
                route["terminal_review"]["terminal_state"],
            )
            self.assertEqual(
                catalog_entry["human_actions"],
                route["terminal_review"]["human_actions"],
            )
            self.assertEqual(
                catalog_entry["may_publish"],
                route["terminal_review"]["may_publish"],
            )

    def test_example_enables_generic_routes_while_other_profiles_remain_unchanged(
        self,
    ) -> None:
        self.assertEqual(
            self.example_profile["profile_revision"], "example-project-profile-v2"
        )
        self.assertTrue(
            NEW_ROUTE_IDS.issubset(set(self.example_profile["enabled_routes"]))
        )
        self.assertEqual(
            set(self.example_profile["role_ids"]),
            {entry["role_id"] for entry in self.registry["roles"]},
        )
        # Private profiles are optional, but their isolation remains checked
        # whenever they are present in the workspace.
        for profile in self.non_example_profiles:
            self.assertTrue(profile["profile_revision"].endswith("-profile-v2"))
            self.assertEqual(
                NEW_ROUTE_IDS.intersection(profile["enabled_routes"]), set()
            )

    def test_audience_deep_research_has_exact_research_and_synthesis_owners(
        self,
    ) -> None:
        route = self.routes["audience-deep-research"]
        by_id = {node["node_id"]: node for node in route["nodes"]}
        self.assertEqual(by_id["audience-research"]["role_id"], "evidence-research")
        self.assertEqual(
            by_id["audience-research"]["deliverable_contract"],
            "audience-research-package@1",
        )
        self.assertEqual(by_id["audience-synthesis"]["role_id"], "audience-voc")
        self.assertEqual(
            route["final_product"],
            {
                "producer_node": "audience-synthesis",
                "contract": "audience-change-proposal@1",
                "supporting_contracts": ["audience-research-package@1"],
            },
        )

    def test_new_routes_are_project_neutral(self) -> None:
        for route_id in sorted(NEW_ROUTE_IDS):
            raw = json.dumps(self.routes[route_id], ensure_ascii=False).lower()
            self.assertNotIn("mavery", raw)
            self.assertNotIn("projects/", raw)


if __name__ == "__main__":
    unittest.main()
