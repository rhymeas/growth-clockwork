# Portable runtime contracts

The candidate SQLite broker contract is documented in [task-broker-v1.md](task-broker-v1.md)
and defined by `task-broker-v1.sql`. It is not connected to the active runtime yet.

`contracts/schemas/` is the canonical, product-neutral source for Coordinator,
workhorse, and approved-release boundary contracts. Project profiles pin
byte-identical copies explicitly; these source files are never selected as an
implicit fallback.

| Contract | Purpose |
| --- | --- |
| `coordinator-plan@1` | Binds one Codex diagnosis and route decision to an exact project, context, request, and `fixture` or `live` run mode. |
| `task-envelope@2` | Binds one executable route step to prerequisite task IDs plus exact role, route, context, and input revisions. |
| `result-envelope@2` | Returns one project-neutral task result whose output and evidence references carry revision and SHA-256 pins. |
| `run-manifest@2` | Records an immutable manifest revision, its predecessor, complete planned route, materialized tasks/results, and review state. |
| `project-memory-record@1` | Records one immutable, project-scoped fact, decision, or learning with exact evidence and lifecycle metadata. |
| `growth-operating-contract@1` | Binds objective, outcome ownership, decision rules, authority, proof, source, and finite resource limits. |
| `code-graph-index@1` | Describes one optional rebuildable code index tied to exact source revision, adapter, license, and coverage. |
| `platform-observation@1` | Preserves one platform observation's access class, query, sample, missingness, privacy, retention, and source identity. |
| `audience-research-package@1` | Binds a research synthesis to exact platform observations, contradictions, limits, and finding evidence. |
| `release-package@1` | Binds one exact receipted human approval to immutable release bytes without credentials or a publisher. |
| `adapter-request@1` | Passes a pinned release package to one explicitly selected versioned adapter and target. |
| `adapter-receipt@1` | Records what an adapter actually did, including external-side-effect and public-live flags. |
| `live-proof@1` | Separates verified destination state from adapter execution and local build claims. |
| `release-outcome@1` | Records the observation window, metrics or explicit absence, and non-causal outcome disposition. |

`postiz-connector-v1.md` defines the optional external delivery boundary around
these contracts. It is prose plus enforced Python behavior rather than another
project schema: the connector consumes an already verified `release-package@1`,
stores private intent/receipt records, and never places credentials in artifacts.
`material-packet-v1.md` defines how selected local files remain hash-bound from
Studio intake through the same human review and into that connector.

Project registries also pin the portable Context Pack, outbox review, human action,
learning, and compatibility schemas used by that profile.

`professional-deliverable.schema.json` and
`deliverable-requirements.json` define the semantic output boundary. The current
registry has 29 requirements, and every output declared by the 19 portable role
contracts resolves to one of them.

`research-adapter-registry.json` is a pinned capability registry, not a network
collector. Its ten entries cover TikTok, YouTube, Instagram, Pinterest, and X:
manual evidence intake is the only `implemented` acquisition adapter; five
official/owner entries are `contract_only`; four OSS or exploratory analysis
entries are `reference_only`. Those statuses must not be promoted to live proof.

All schemas use only the JSON Schema subset implemented by
`pipeline.validate_json_suites.SubsetValidator`; they deliberately contain no
`$ref` or `$defs`.

Runtime `artifact_ref` values are paths inside the selected project's isolated
state namespace. Code verifies every referenced file's actual bytes, revision,
SHA-256, and completed Root Writer receipt before use. JSON Schema alone cannot
prove filesystem identity, role membership, route membership, unique step IDs,
DAG acyclicity, dependency satisfaction, profile write authority, Writer receipts,
human authorship, or that live runs exclude fixture/synthetic context. The
Context Resolver, Agent Registry, Coordinator, Run Engine, Runtime Status, Review
API, Rework layer, and Release Core therefore fail closed on their respective
semantic checks.

All routes end at Governance and an `outbox-review-item@1`. The control plane has
no publisher credentials. The current local filesystem adapter is restricted to
profiles explicitly marked `synthetic-fixture` and records
`publicly_live: false` and `external_side_effects: false`.

Canonical memory records may feed the implemented rebuildable SQLite FTS5 view.
The code-graph schema has an assessment boundary but no completed real repository
pilot. Learning closure may append exact memory plus an immutable next-decision
proposal; that proposal cannot schedule or execute itself.
