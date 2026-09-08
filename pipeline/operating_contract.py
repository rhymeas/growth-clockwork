#!/usr/bin/env python3
"""Semantic enforcement for one bounded Growth OS operating contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from pipeline.validate_json_suites import (
    SuiteConfigurationError,
    validate_registered_instance,
)


SCHEMA_ID = "growth-operating-contract@1"


class OperatingContractError(ValueError):
    """Raised when work lacks a safe, decision-ready operating contract."""


def validate_operating_contract(
    profile_path: Path,
    contract: dict[str, Any],
    *,
    route_role_ids: set[str] | None = None,
) -> None:
    try:
        errors = validate_registered_instance(profile_path, SCHEMA_ID, contract)
    except SuiteConfigurationError as exc:
        raise OperatingContractError(str(exc)) from exc
    if errors:
        first = errors[0]
        raise OperatingContractError(
            f"Operating contract rejected {first.instance_path}: {first.message}"
        )

    allowed = set(contract["authority"]["allowed_actions"])
    forbidden = set(contract["authority"]["forbidden_actions"])
    overlap = sorted(allowed & forbidden)
    if overlap:
        raise OperatingContractError(
            f"Actions cannot be both allowed and forbidden: {', '.join(overlap)}"
        )
    owner = contract["outcome_owner"]["role_id"]
    if owner == "governance-reviewer":
        raise OperatingContractError(
            "The independent governance reviewer cannot own the growth outcome"
        )
    if route_role_ids is not None and owner not in route_role_ids:
        raise OperatingContractError(
            "The single outcome owner must be an active role in the selected route"
        )
    unofficial = contract["authority"]["may_use_unofficial_collection"]
    grades = set(contract["data_policy"]["allowed_evidence_grades"])
    if unofficial and "exploratory" not in grades:
        raise OperatingContractError(
            "Unofficial collection requires an explicit exploratory evidence grade"
        )
    if not unofficial and "exploratory" in grades:
        raise OperatingContractError(
            "Exploratory evidence is allowed only when unofficial collection is declared"
        )
    required_proof = {
        "source_hashes",
        "professional_contract_checks",
        "governance_verdict",
        "human_exact_revision",
    }
    missing = sorted(required_proof - set(contract["proof_requirements"]))
    if missing:
        raise OperatingContractError(
            f"Operating contract lacks mandatory proof: {', '.join(missing)}"
        )


def aggregate_usage(results: Iterable[dict[str, Any]]) -> dict[str, float | int | None]:
    totals: dict[str, float | int | None] = {
        "duration_ms": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "variable_external_cost_eur": 0.0,
    }
    for result in results:
        usage = result["usage"]
        for field in totals:
            if usage[field] is None or totals[field] is None:
                totals[field] = None
            else:
                totals[field] += usage[field]
    return totals


def enforce_resource_limits(
    contract: dict[str, Any],
    totals: dict[str, float | int | None],
) -> None:
    limits = contract["resource_limits"]
    comparisons = (
        ("duration_ms", "max_duration_ms"),
        ("input_tokens", "max_input_tokens"),
        ("output_tokens", "max_output_tokens"),
        ("variable_external_cost_eur", "max_variable_external_cost_eur"),
    )
    exceeded = [
        name
        for name, limit_name in comparisons
        if totals[name] is not None and totals[name] > limits[limit_name]
    ]
    if exceeded:
        raise OperatingContractError(
            f"Run resource limit exceeded: {', '.join(exceeded)}"
        )


def enforce_run_limits(
    contract: dict[str, Any],
    results: Iterable[dict[str, Any]],
    *,
    source_refs: Iterable[str],
) -> dict[str, float | int | None]:
    """Fail closed when accepted work would exceed a run's finite budget.

    Metered providers must expose all counters. An explicitly selected native
    Codex subscription lane may omit token counters that the host does not
    expose. Those totals stay unknown, never zero or falsely budget-verified.
    That lane still requires known zero variable external spend and enforces
    the finite duration, source and per-task attempt limits.
    """

    materialized = list(results)
    limits = contract["resource_limits"]
    unknown = sorted(
        {
            field
            for result in materialized
            for field in (
                "input_tokens",
                "output_tokens",
                "variable_external_cost_eur",
            )
            if result["usage"][field] is None
        }
    )
    native_codex = limits.get("usage_policy", "metered") == "codex_subscription"
    if native_codex and any(
        result.get("provenance", {}).get("provider") != "codex"
        or result["usage"]["variable_external_cost_eur"] != 0
        for result in materialized
    ):
        raise OperatingContractError(
            "Codex subscription usage requires native codex provenance and known zero external cost"
        )
    if unknown and not native_codex:
        raise OperatingContractError(
            "Run usage is unmetered: " + ", ".join(unknown)
        )

    excessive_attempts = [
        result["task_id"]
        for result in materialized
        if result["attempts"] > limits["max_attempts_per_task"]
    ]
    if excessive_attempts:
        raise OperatingContractError(
            "Run attempt limit exceeded: " + ", ".join(sorted(excessive_attempts))
        )

    distinct_sources = {ref for ref in source_refs if ref}
    if len(distinct_sources) > limits["max_sources"]:
        raise OperatingContractError(
            "Run source limit exceeded: "
            f"{len(distinct_sources)} > {limits['max_sources']}"
        )

    totals = aggregate_usage(materialized)
    enforce_resource_limits(contract, totals)
    return totals
