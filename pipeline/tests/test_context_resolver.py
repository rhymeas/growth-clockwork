from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from pipeline.context_resolver import (
    ContextResolutionError,
    DISCOVERY_CONTEXT_KINDS,
    resolve_project_context,
)
from pipeline.tests.draft_project_fixture import (
    MANIFEST_NAME, create_draft_project, rewrite_context_section,
)


WORKSPACE = Path(__file__).resolve().parents[2]


def context_pointer(profile_root: Path, manifest_name: str) -> dict[str, str]:
    path = profile_root / "context/manifests" / manifest_name
    value = json.loads(path.read_text(encoding="utf-8"))
    return {
        "artifact_ref": f"context/manifests/{manifest_name}",
        "artifact_revision": value["context_revision"],
        "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


class ProjectContextResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.draft_profile = create_draft_project(Path(self.temporary.name))

    def test_discovery_retains_draft_and_blocked_sections_without_approving_them(self) -> None:
        profile_root = self.draft_profile.parent
        resolved = resolve_project_context(
            profile_root / "project.json",
            context_pointer(profile_root, MANIFEST_NAME),
            run_mode="discovery",
            required_kinds=(*DISCOVERY_CONTEXT_KINDS, "channels"),
        )

        self.assertEqual(resolved.value["status"], "draft")
        self.assertEqual(len(resolved.sections), 8)
        self.assertEqual(resolved.sections["facts"].value["usage_policy"], "blocked")
        self.assertEqual(resolved.sections["audience"].value["material_state"], "candidate")
        self.assertEqual(resolved.sections["channels"].value["usage_policy"], "restricted")
        self.assertFalse(resolved.fixture_only)

    def test_discovery_does_not_allow_a_required_blocked_section(self) -> None:
        profile_root = self.draft_profile.parent
        with self.assertRaisesRegex(ContextResolutionError, "facts is not allowed"):
            resolve_project_context(
                profile_root / "project.json",
                context_pointer(profile_root, MANIFEST_NAME),
                run_mode="discovery",
                required_kinds=(*DISCOVERY_CONTEXT_KINDS, "facts"),
            )

    def test_discovery_rejects_blocked_or_restricted_governing_policy(self) -> None:
        for policy in ("blocked", "restricted"):
            with self.subTest(policy=policy), tempfile.TemporaryDirectory() as temporary:
                profile_path = create_draft_project(Path(temporary))
                profile_root = profile_path.parent
                rewrite_context_section(profile_path, "authority", {"usage_policy": policy})
                with self.assertRaisesRegex(ContextResolutionError, "authority is not allowed"):
                    resolve_project_context(
                        profile_root / "project.json",
                        context_pointer(profile_root, MANIFEST_NAME),
                        run_mode="discovery",
                        required_kinds=DISCOVERY_CONTEXT_KINDS,
                    )

    def test_discovery_still_requires_current_required_material(self) -> None:
        profile_root = self.draft_profile.parent
        with self.assertRaisesRegex(ContextResolutionError, "audience is not current"):
            resolve_project_context(
                profile_root / "project.json",
                context_pointer(profile_root, MANIFEST_NAME),
                run_mode="discovery",
                required_kinds=(*DISCOVERY_CONTEXT_KINDS, "audience"),
            )

    def test_discovery_rejects_fixture_pack(self) -> None:
        profile_root = WORKSPACE / "projects/example"
        with self.assertRaisesRegex(ContextResolutionError, "fixture-only context cannot be used"):
            resolve_project_context(
                profile_root / "project.json",
                context_pointer(profile_root, "CTX-EXAMPLE-0001.fixture-r1.json"),
                run_mode="discovery",
                required_kinds=DISCOVERY_CONTEXT_KINDS,
            )

    def test_discovery_verifies_even_excluded_section_and_source_bytes(self) -> None:
        for target in ("section", "source"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary:
                profile_root = create_draft_project(Path(temporary)).parent
                section_path = profile_root / "context/sections/facts/facts-draft-r1.json"
                section = json.loads(section_path.read_text(encoding="utf-8"))
                path = section_path if target == "section" else (
                    profile_root / section["source_refs"][0]["artifact_ref"]
                )
                path.write_text("tampered\n", encoding="utf-8")
                with self.assertRaisesRegex(ContextResolutionError, "hash mismatch"):
                    resolve_project_context(
                        profile_root / "project.json",
                        context_pointer(profile_root, MANIFEST_NAME),
                        run_mode="discovery",
                        required_kinds=DISCOVERY_CONTEXT_KINDS,
                    )

    def test_example_fixture_pack_resolves_all_eight_sections(self) -> None:
        profile_root = WORKSPACE / "projects/example"
        resolved = resolve_project_context(
            profile_root / "project.json",
            context_pointer(
                profile_root, "CTX-EXAMPLE-0001.fixture-r1.json"
            ),
            run_mode="fixture",
        )

        self.assertEqual(resolved.value["status"], "ready")
        self.assertEqual(len(resolved.sections), 8)
        self.assertTrue(resolved.fixture_only)

    def test_fixture_context_is_rejected_for_live_run(self) -> None:
        profile_root = WORKSPACE / "projects/example"

        with self.assertRaisesRegex(
            ContextResolutionError, "fixture-only context cannot be used"
        ):
            resolve_project_context(
                profile_root / "project.json",
                context_pointer(
                    profile_root, "CTX-EXAMPLE-0001.fixture-r1.json"
                ),
                run_mode="live",
            )

    def test_draft_context_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profile_root = Path(temporary) / "example"
            shutil.copytree(WORKSPACE / "projects/example", profile_root)
            manifest_name = "CTX-EXAMPLE-0001.fixture-r1.json"
            manifest_path = profile_root / "context/manifests" / manifest_name
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["status"] = "draft"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ContextResolutionError, "draft, not ready"):
                resolve_project_context(
                    profile_root / "project.json",
                    context_pointer(profile_root, manifest_name),
                    run_mode="live",
                )

    def test_section_byte_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "example"
            shutil.copytree(WORKSPACE / "projects/example", copied)
            section = copied / "context/sections/facts/facts-fixture-r1.json"
            section.write_text("{}\n", encoding="utf-8")

            with self.assertRaisesRegex(ContextResolutionError, "hash mismatch"):
                resolve_project_context(
                    copied / "project.json",
                    context_pointer(
                        copied, "CTX-EXAMPLE-0001.fixture-r1.json"
                    ),
                    run_mode="fixture",
                )

    def test_source_symlink_is_rejected_even_inside_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "example"
            shutil.copytree(WORKSPACE / "projects/example", copied)
            evidence = (
                copied
                / "context/evidence/synthetic-evidence-index-fixture-r1.json"
            )
            replacement = copied / "context/evidence/replacement.json"
            replacement.write_bytes(evidence.read_bytes())
            evidence.unlink()
            evidence.symlink_to(replacement)

            with self.assertRaisesRegex(ContextResolutionError, "symlink"):
                resolve_project_context(
                    copied / "project.json",
                    context_pointer(
                        copied, "CTX-EXAMPLE-0001.fixture-r1.json"
                    ),
                    run_mode="fixture",
                )


if __name__ == "__main__":
    unittest.main()
