# Modular Growth Pipeline core

This directory is the product-neutral control plane for Growth Clockwork. It
does not contain product facts, task prefixes, channels, accounts, credentials,
or publication targets. Project identity and truth live below
`projects/<profile>/`; reusable role and route contracts live below `agents/`.

```text
pipeline/
  autopilot.py              # saved brief -> resume exact native Codex work orders
  workhorse.py              # bind authored payloads/evidence; never generate fixture content
  context_resolver.py       # exact Project Context Pack resolution
  agent_registry.py         # hash-pinned role and route resolution
  professional_contracts.py # 29 role-specific deliverable requirements
  coordinator.py            # compile one selected route and first DAG wave
  run_engine.py             # accept role results and advance the DAG
  runtime_status.py         # read-only proven resume/status view
  root_writer.py            # only canonical state writer
  project_start.py          # immutable Project Briefs + research-readiness read model
  goal_loop.py              # durable, local-only starting check for one saved brief
  feed_intake.py            # bounded RSS/Atom from project-local research-feeds.json
  weekly_cycle.py           # weekly receipt -> one strict idle-only content route
  macos_autostart.py        # Desk login service + weekly local curator schedule
  project_memory.py         # immutable records + rebuildable SQLite FTS5 view
  code_graph.py             # optional code-index receipt assessment boundary
  audience_research.py      # source-bound audience package validation
  research_adapters.py      # pinned capability registry; no hidden collector
  operating_contract.py     # bounded authority, proof, and resource limits
  learning_loop.py          # outcome -> memory + next-decision proposal
  validate_json_suites.py   # project-confined schema fixtures
  review_api.py             # local exact-revision human review boundary
  inbox_worker.py           # deterministic Studio proposal router
  research_worker.py        # read-only public-web evidence packet -> Marketing
  marketing_worker.py       # evidence-bound channel draft -> Mavery QA
  mavery_qa_worker.py       # exact-byte policy/claim critique -> human Review
  broker_automation.py      # one serial local Research/Marketing/QA lane
  publisher_worker.py       # durable exact-package Postiz delivery task
  rework.py                 # note -> new immutable full-route run
  release_core.py           # approve -> credential-free release package
  release_adapter.py        # versioned adapter port; fixture adapter only
  adapters/
  tests/
projects/<profile>/
  project.json
  context/
  schemas/
  fixtures/
  state/                    # isolated canonical runtime state
dashboard/                  # local Review Desk; no publisher
```

Adding a product means adding another profile, Context Pack, and fixture suite.
Do not fork the Python control plane.

## Native Codex production

Use `python3 -m pipeline.autopilot --workspace . --project-config
projects/<profile>/project.json` to start/resume a saved brief. This deterministic
driver emits exact work orders; the Codex root task dispatches native role agents
and submits authored bundles with `pipeline.workhorse`. It is not an LLM client or
an always-on server. A Codex app heartbeat must actually run the skill to execute
the orders. Missing external API keys or Make do not block native production.

The driver consumes review notes once, carries original source lineage and finite
operating limits into rework, pauses at the exact final product, and does not
duplicate an already approved or declined brief. Inspect with `--inspect`.
Project Desk's `automation` field projects this verified execution state without
exposing machine work orders; context-readiness remains a separate input check.

Explicit discovery supports research and complete educational content with draft
product knowledge. It preserves hashes, governed policy and uncertainty, and does
not approve missing product facts or enable blocked launch/measurement routes.

Native subscription runs may explicitly select `codex_subscription` usage policy:
unexposed tokens stay null, external spend must be known zero, and other resource
limits still apply. A token cap is not verified when counters are unexposed.

Optional feed selection lives in `projects/<profile>/research-feeds.json`, an array
of `[publisher, https_feed_url]` pairs (1–10 unique URLs). No project gets another
project's feeds by default. Collection requires `--project`; configuration and the
saved brief both bind the weekly receipt identity. Feed reads never collect on a
dashboard GET and never constitute validated audience demand.

## Project start and research readiness

The loopback Project Desk records one operator-authored
`project-start-brief@2` per starting revision. It stores the goal, audience,
success signal, baseline status, working frame, non-goals, and bounded research
question as an immutable project-scoped record. Changes create another brief;
they never edit an earlier one.

`project-start-readiness@1` records the evidence-bound progression for the exact
brief through five steps: baseline, Project Context, audience research, evidence,
and first recommendation. An explicit `not_measured` baseline blocks impact,
experiment, and launch claims but does not stop qualitative research. A qualified
recommendation with that blocker is `research_recommendation_ready`, not
`ready_to_queue`, approval, or go-live.

The Desk's Project Start endpoints only create the brief and read its customer-safe
progress view. They do not call Codex, queue a Coordinator plan, create a final
artifact, publish, or hold credentials.

## Local starting check

After a Project Brief exists, the Desk can run one small durable local check. It
records an immutable request under `records/goal-loops/`, verifies the exact
pinned Project Context Pack, then records an immutable result. A queued request
resumes when the loopback service next reads its status, including after a local
service restart.

The result intentionally says only what this check knows: whether the pinned
context is ready, draft, or missing; that external evidence was not collected;
and that public actions remain protected. It cannot research the web, call a
model, create a draft, invoke analytics, queue a route, publish, or use a
credential. It is a visible first hand-off, not a substitute for audience
research or a real autonomous agent cycle.

## Runtime loop

Codex diagnoses one operator-authored objective and writes a
`coordinator-plan@1`. The deterministic Coordinator verifies that plan against
the selected profile, Context Pack, role registry, and route registry. It
snapshots the exact inputs and materializes only the first executable DAG wave:

```bash
python3 -m pipeline.coordinator prepare \
  --workspace /absolute/path/to/workspace \
  --project-config /absolute/path/to/projects/<project>/project.json \
  --plan /absolute/path/to/coordinator-plan.json
```

Codex workhorses receive `task-envelope@2` and return `result-envelope@2`. They
do not write project state directly. The Root accepts each result through the
run engine:

```bash
python3 -m pipeline.run_engine accept \
  --workspace /absolute/path/to/workspace \
  --project-config /absolute/path/to/projects/<project>/project.json \
  --submission /absolute/path/to/result-envelope.json
```

The engine verifies task identity, exact input and output hashes, completed Root
Writer receipts, role write roots, and DAG dependencies. It then materializes
the next ready wave. Parallel route nodes may be ready together. Only a final
Governance pass can copy exact producer bytes into `outbox/artifacts/`, create
lineage, and move the run to `awaiting_review`. The engine has no model, network,
shell, scheduler, publisher, or external-account path.

## Read-only status and resume

Inspect exactly one selected project without changing state:

```bash
python3 -m pipeline.runtime_status \
  --workspace /absolute/path/to/workspace \
  --project-config /absolute/path/to/projects/<project>/project.json
```

The inspector reports only receipt-covered, schema-valid manifest chains. It
returns dispatchable queued tasks or the exact review pointer for an
`awaiting_review` run. A tampered, stale, cross-project, symlinked, or
unreceipted latest revision rejects the selected project view. It never repairs
or advances a run.

## Schema suite contract

Run a suite only with an explicit project profile:

```bash
python3 -m pipeline.validate_json_suites \
  --project-config projects/example/project.json \
  projects/example/fixtures/schema-contracts/suite.json
```

The standard-library runner confines the suite, registry, schemas, and fixtures
to the selected profile. Registry SHA-256 pins must match, and every fixture
must carry the selected `project_id` and `project_profile_revision`. Exit codes
are `0` for a passing suite, `1` for an expectation mismatch, and `2` for a
malformed, unpinned, cross-project, or unsupported input.

The checked-in synthetic Example report currently records 44/44 expected
outcomes. This is a schema-fixture receipt, not proof of a real project run or
external publication. Installed product profiles validate independently.

## Root Writer contract

All canonical runtime persistence goes through `pipeline.root_writer`. A request
pins project, profile revision, run, idempotency key, exact bytes, expected
hashes, and allowlisted project-relative destinations. Optional preconditions
are evaluated under the same project-state lock: `expected_sha256: null`
requires absence; a lowercase SHA-256 requires exact current bytes.

```bash
python3 -m pipeline.root_writer \
  --workspace /absolute/path/to/workspace \
  --project-config /absolute/path/to/projects/<project>/project.json \
  /absolute/path/to/write-request.json
```

Writes are immutable exclusive creates. Editing means a new revision path.
Traversal uses directory file descriptors and `O_NOFOLLOW`; concurrent targets
are preserved and rejected. Every completed request has an idempotent readback
receipt below the selected state at `clockwork/run-receipts/<run-id>/`.
Incomplete multi-file writes remain inert without a completed receipt and are
never promoted as successful state.

The writer accepts no delete, model, shell, network, or publish operation. The
current primitive targets POSIX systems such as macOS and Linux. A Windows port
needs equivalent exclusive-create and no-reparse-point guarantees.

## Registered portable contracts

The current runtime uses product-neutral contracts for:

- Context sections and `project-context-manifest@1`;
- `project-start-brief@2` and `project-start-readiness@1`;
- `coordinator-plan@1`;
- `task-envelope@2`, `result-envelope@2`, and `run-manifest@2`;
- `outbox-review-item@1` and `human-review-action@1`;
- `learning-record@1`, `project-memory-record@1`, and
  `growth-operating-contract@1`;
- `code-graph-index@1`, `platform-observation@1`, and
  `audience-research-package@1`; and
- `release-package@1`, `adapter-request@1`, `adapter-receipt@1`,
  `live-proof@1`, and `release-outcome@1`.

Profiles pin byte-identical copies of the shared contracts. Older v1 task and run
schemas remain registered for compatibility. A profile may retain additional
legacy contracts without adding them to the reusable control plane. Registry
hashes pin every schema to its exact bytes.

## Local human review boundary

Start the loopback-only API:

```bash
python3 -m pipeline.review_api \
  --workspace /absolute/path/to/workspace
```

The API exposes project discovery, Project Desk read and start-brief endpoints,
the local starting-check read/start endpoints, pending reviews, exact evidence
reads, and one terminal review-action endpoint on `127.0.0.1`. It verifies the pending
manifest, reviewed artifact bytes, evidence path confinement, and declared
SHA-256 before showing content. UTF-8 evidence opens in the sleek
project-switching Review Desk. A material packet can resolve receipt-verified image,
video or audio bytes in the same review; arbitrary binary evidence still fails closed
with `render_adapter_required`.

The human's only final actions are:

- `approve`: authorize the exact visible bytes for the approved-only release
  boundary;
- `decline`: terminally reject that revision without deleting it; or
- `note`: preserve it and request a new immutable revision.

Every action pins project, profile revision, artifact ID, revision, and SHA-256
and is stored through Root Writer. The system cannot manufacture a human action.
An identical retry is idempotent; a conflicting terminal action is rejected.

## Note to rework

```bash
python3 -m pipeline.rework \
  --workspace /absolute/path/to/workspace \
  --project-config /absolute/path/to/projects/<project>/project.json \
  --action-ref outbox/review-actions/<artifact-id>/<revision>.json
```

Only an exact, receipted human `note` can prepare rework. The adapter verifies
the action, lineage, reviewed bytes, source manifest chain, and receipts, then
reopens the same route with the same pinned context and byte-identical role and
route contracts. The new first-wave task pins the note and prior artifact. The
old artifact never changes. An atomic absence precondition prevents a concurrent
follow-up review action from racing the new run.

Preparation does not call an agent or publish. The normal Codex workhorse loop
continues the new run.

## Approve to release and outcome

`pipeline.release_core` accepts only an exact, receipted human `approve` whose
project, revision, reviewed bytes, lineage, and origin all agree. It creates an
immutable credential-free release package. The loopback Review API now performs
this reconciliation and packaging directly after the approval is saved.

The versioned adapter port can then produce an adapter receipt, live-proof
record, and outcome record. The only shipped implementation is
`local-filesystem-synthetic@1`, restricted to the synthetic Example profile. Its
preview and simulated-live targets stay local and always record
`external_side_effects: false` and `publicly_live: false`. Every non-fixture
profile is rejected by that fixture adapter.

A real publisher remains separate from the credential-free adapter port.
`pipeline.postiz_connector` is the first optional delivery boundary: it re-verifies
one approved release package, intersects current local permissions, accepts its API
key only from `POSTIZ_API_KEY`, and records an immutable intent before an external
request. `review` mode permits drafts only; scheduled/immediate delivery needs
`automatic`. An uncertain request is never blindly repeated. See
`contracts/postiz-connector-v1.md`.

The connector remains callable as a CLI. When both local publisher switches are
enabled, the Review API instead writes one immutable publisher request and queues a
broker task after approval; it never waits on Postiz. The single-lane worker resumes
queued publisher tasks after restart even when Codex is unavailable. `review`
creates a Postiz draft. `automatic` schedules a future planned slot or submits a due
slot; without a planning slot it still creates only a draft. Ambiguous external
outcomes fail for reconciliation and are never automatically retried. Today no
Postiz service, destination, credential or public live-verification adapter is
configured, so the current runtime still cannot publish externally. The inactive
connector code supports review-bound JPEG, PNG, WebP and MP4 upload.

Project Desk's contextual Background tasks disclosure shows recent publisher
states. A failed publisher task is labeled `Reconciliation required` and offers no
blind retry. The operator must first determine from Postiz/platform history whether
the ambiguous request already happened. This is visibility, not automated external
reconciliation.

## Current proof boundary

- The portable registry contains 19 roles and 11 route DAGs. Every declared
  role output maps to one of 29 professional deliverable requirements, and
  structural conformance checks cover all registered routes.
- Checked-in schema reports cover 44/44 expected Example cases and 60/60
  expected Mavery cases.
- The synthetic Example profile has one persisted `research-evidence` run in
  `awaiting_review`; its final conclusion is `insufficient_evidence`, not an
  invented winner.
- Persisted synthetic demonstrations also cover market-positioning,
  product-funnel-experiment, and content-channel. They are examples; route
  registration and professional-contract conformance cover the full 11-route
  catalog without pretending that synthetic data is a market result.
- A temporary-state synthetic `audience-deep-research` pilot also reaches exact
  review through Governance. It proves the route mechanics, not live audience
  data or a market finding.
- Project memory has an implemented rebuildable SQLite FTS5 cache with
  project/profile and source-revision checks. Learning closure writes immutable
  memory and a next-decision proposal, but never schedules or executes it.
- The research registry records ten candidates across TikTok, YouTube,
  Instagram, Pinterest, and X. Manual evidence is the only implemented
  acquisition path; its executable intake verifies project identity and exact
  supplied evidence hashes without network or persistence authority.
  Official/owner API integrations are contract-only and OSS analysis projects
  are reference-only.
- Code-graph schema and assessment logic exist, but no actual repository index
  pilot has been completed.
- Note-to-rework and approve-to-release-to-proof-to-outcome are executable and
  tested only through local synthetic fixtures.
- Product-profile readiness is deliberately outside this portable package and
  must be proven by the selected profile's own Context Pack and schema suite.
- A Project Brief is a qualified starting input, not a scheduler command. An
  explicit bounded handoff to a Codex coordinator plan remains unconfigured.
- Binary evidence still needs a render adapter. Real cross-platform acquisition,
  external publication, automatic follow-up execution, and post-publication
  measurement remain unconfigured proof layers.
