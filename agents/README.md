# Portable Growth Agent Registry

This directory defines reusable Growth roles and route DAGs. It contains no
product facts, task prefixes, channel accounts, credentials, customer data, or
publication target.

## Runtime model

Codex is the orchestrator. A Codex Root task binds exactly one project profile
and one immutable Project Context Manifest, selects one route, then starts only
the required Codex workhorses. Workhorses receive `task-envelope@2`, operate
without direct persistence or external side effects, and return
`result-envelope@2`. The Root validates and persists accepted results through the
Root Writer.

Roles are capability contracts, not permanent processes. A project enables the
roles its motion needs. A route is a versioned DAG over those roles, not a prompt
chain or an implicit conversation.

The current portable catalog contains 19 roles and 11 registered route DAGs.
Every role-declared output resolves to one of 29 professional deliverable
requirements. A profile may enable a strict subset; registration does not imply
that a live project has data, credentials, or market evidence for that route.

## Activation modes

- `always_control`: control roles required for every run. Coordinator and
  Governance use this mode.
- `required_by_route`: started only when the selected route names the role.
- `mandatory_by_product_motion`: enabled by the project profile before a
  meaningful route can run. Product-led software, for example, needs
  `growth-product-cro`; an API product may require `developer-relations`.
- `activated_by_observed_trigger`: optional specialization or extra capacity
  enabled only by evidence recorded in the project context.

Numeric thresholds alone do not decide activation. They may support an observed
trigger, but cannot postpone the first professional preflight. A product-led
profile cannot wait for several funnel learnings before assigning an owner to the
first funnel experiment.

## Route boundary

Every route:

1. starts from a project-bound objective;
2. uses explicit role nodes and immutable input references;
3. ends with an isolated Governance node reviewing the exact final-product
   revision;
4. becomes `review_ready` only after a `pass` verdict; and
5. creates an `outbox-review-item@1` for `approve`, `decline`, or `note`.

There is no publish node. Approval is an exact-revision human disposition, not a
publication command. A later publisher may be a separate adapter, but it is not
present in this registry and can never be called by a role or route.

`note` is a control-plane transition: the next Root run reopens the original
route at the affected owner and produces a new immutable revision. It is not a
separate specialist role and never mutates the reviewed artifact.

Optional nodes are profile-bound. If an optional specialist is unavailable or
its condition is false, the Root records a deterministic skipped receipt; a
downstream dependency treats that receipt as satisfied, not as fabricated work.

## Why external systems stay adapters

GitHub may provide versioned backup or a code-review surface. Linear may provide
roadmap and priority views. Make may provide a small deterministic import or
export. None is required to load this registry, select a route, run a workhorse,
or record a local review action.

Those tools remain adapters because:

- they must not become a second source of project truth;
- their availability, pricing, permissions, and APIs can change;
- a task tracker status is not an approval;
- an automation transport is not a professional decision; and
- no connector may broaden a role's network, send, spend, account, or publish
  authority.

## Layout

```text
agents/
  registry.json
  catalog.json
  schemas/
    catalog.schema.json
    registry.schema.json
    role-spec.schema.json
    route-spec.schema.json
  roles/
    core/
    specialists/
  routes/
```

`registry.json` is the versioned discovery entrypoint. Every role and route spec
is pinned by the SHA-256 of its exact bytes. `catalog.json` repeats the ordered
route steps, required context, `result-envelope@2` boundary, semantic deliverable
type, and Root-writable paths so a coordinator can load one hash-verified file
before resolving detailed specs.

Project profiles may register a subset of the catalog's role and route IDs. They
must never fork these generic specs to add product facts; product truth belongs in
the selected Project Context Pack.

The catalog includes an `audience-deep-research` route, but acquisition remains
adapter-bound. The pinned ten-entry research registry covers TikTok, YouTube,
Instagram, Pinterest, and X. Only manual evidence intake is currently
implemented; official/owner APIs are contract-only and OSS analysis projects are
reference-only. Unofficial sources remain disabled, noncanonical, and limited to
explicit exploratory idea generation.
