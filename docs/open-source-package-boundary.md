# Open-source package boundary

Status: Apache-2.0 public source repository and early-alpha GitHub Pre-release.

## Decision

The reusable Growth Clockwork should become its own project-neutral package.
This mixed Mavery workspace must not receive a repository-wide license. The
machine-readable boundary is [`open-source-package.json`](../open-source-package.json).
It defines what may be copied into a future clean repository and what must stay
behind.

This file and the manifest do **not** make the current mixed repository or any
excluded Mavery files open source. The extracted package carries Apache-2.0 and
is public at <https://github.com/rhymeas/growth-clockwork>. The first public source
tag is `v0.1.0-alpha.23`.

## Public package

The public package contains only the reusable control plane and a synthetic
reference project:

| Included root | Purpose |
| --- | --- |
| `pipeline/` | Project-neutral Coordinator, Run Engine, Root Writer, validation, review, rework, status, and release boundaries plus their tests. |
| `agents/` | Portable role contracts and route DAGs. |
| `contracts/` | Canonical shared schemas. |
| `dashboard/` | Local human Review Desk source and tests. Generated dependencies, builds, and design-proof screenshots are excluded. |
| `projects/example/` | Synthetic, fixture-only reference profile and Context Pack. Its live runtime state is excluded. |
| `skills/growth-clockwork/` | Codex entrypoint for operating the modular pipeline. |
| This document and `open-source-package.json` | Human-readable and machine-readable package boundary. |
| Standalone root files | Package README, Apache-2.0 license, contribution and security guidance, third-party notice, ignore rules, and a safe runtime-permission template. |

The synthetic Example profile may demonstrate complete routes. It must remain
`fixture_only`, non-publishable, and free of Mavery claims, customer material,
real analytics, credentials, or account identifiers.

## Explicitly outside the package

- All of `projects/mavery/`: product profile, Context Pack, facts, schemas,
  fixtures, and any state.
- Every project runtime `state/`: receipts, task handoffs, records, staging,
  evidence packets, outbox items, review decisions, release receipts, live proof,
  and learning outcomes.
- Workspace marketing data: `facts/`, `policy/`, `research/`, `content/`,
  `metrics/`, `published/`, `evidence/`, and `templates/`.
- Provider and repository configuration: `providers/` and `.github/`.
- Generated files: dependency trees, dashboard builds, caches, temporary writer
  state, and operating-system metadata.
- Credentials and secret-like files, including `.env`, private keys,
  certificates, credential JSON, and secret JSON.

Exclusion wins over inclusion. This matters because `projects/example/` is an
included root while `projects/example/state/` is forbidden.

## License boundary

`Apache-2.0` is selected for the clean extracted package because it is permissive
and includes an explicit patent grant. The license file is copied to the candidate
root. It does not apply to this mixed repository or anything outside the manifest
boundary. Dashboard dependency licenses remain their own; Apache-2.0 does not
replace them.

The optional GitNexus navigation pilot is documented under `research/` and its
PolyForm Noncommercial 1.0.0 dependency is explicitly excluded from this
Apache-compatible candidate package. It must not become an implicit exported
runtime dependency.

## Testable boundary

`pipeline/tests/test_open_source_package.py` validates that:

- the manifest has the expected contract and conservative non-release status;
- every path is relative, normalized, unique, and free of traversal or globs;
- every included path exists;
- required reusable roots are present;
- private/project roots and real runtime state are explicitly excluded;
- expanded candidate files cannot cross into an excluded root; and
- symlinks, caches, generated output, and credential-like files cannot enter the
  export set;
- portable text cannot reference the excluded product profile, its project ID,
  or its task-ID prefix; and
- concrete repository-relative files named by the Codex skill must also be part
  of the candidate export.

The test proves scope discipline in this working tree. It is not a legal,
copyright, dependency-license, secret-scanning, or supply-chain audit.

`pipeline.package_audit` adds a bounded technical check for an extracted candidate.
It verifies every receipt hash and byte size, rejects unreceipted source files and
symlinks, scans portable UTF-8 text for high-confidence credential shapes and macOS
user paths, and inventories every locked dashboard dependency license. Current
accepted dependency identifiers are MIT, MIT-0, ISC, Apache-2.0, BSD-2-Clause,
BSD-3-Clause and MPL-2.0. Any new or missing identifier fails closed for review.

```bash
python3 -m pipeline.package_audit /absolute/path/to/candidate
```

This pattern scan does not replace a secret-history scan, legal review or complete
license-notice generation. MPL-2.0 components retain their own file-level terms.

## Public release status

The public repository and packaged Pre-release exist. Their preparation gates were:

1. Extract exactly the manifest-selected files into a clean directory. Do not
   copy the whole Mavery workspace and then delete private material. **Implemented.**
2. Re-run all Python, schema, dashboard, route, review, and synthetic release
   proofs from the extracted tree.
3. Run `pipeline.release_gate` with Gitleaks plus the bounded personal-data scan on
   the extracted tree and its complete history. **Implemented and proven locally.**
4. Review the generated dependency inventory and media provenance. The normalized
   CycloneDX SBOM is implemented; human notice/media-provenance review remains.
5. Replace workspace-specific wording and absolute local paths. Add a clean
   package README, installation path, contribution rules, security reporting
   process, compatibility statement, and versioning policy. **Candidate metadata implemented.**
6. Keep the Apache-2.0 license confined to the extracted package. **Implemented.**
7. Create the first packaged GitHub Pre-release with checksums and reproducible
   local validation. **Implemented for `v0.1.0-alpha.25`.** GitHub is hosting, not
   runtime authority, and no paid GitHub feature is required.

The accurate description is **public early-alpha source repository and packaged
Pre-release**. It is not a hosted service or a claim that optional integrations
have been configured.
