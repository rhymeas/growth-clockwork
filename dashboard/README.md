# Project Desk

One local platform for Growth Clockwork: **Project**, **Research**,
**Content & review**, and **Insights**. The design and consolidation boundaries
are in [DESIGN.md](DESIGN.md). There is no separate Growth Desk app or review queue.

A project-neutral workspace for starting and progressing a Growth OS project.
It presents the current goal, audience, success signal, non-goals, research
readiness, blockers, and next responsible roles. Its **Review** section keeps
the exact immutable revision workflow: approve, decline, or request a recreated
revision. It does not publish.

## Run

```bash
npm install
npm run desk
```

This starts the local workspace service and the Desk together, so the review
and project-start views never point at a missing local API. The Vite dev server
proxies `/api` to `http://127.0.0.1:8765`.

For a single-process built Desk suitable for local background startup:

```bash
npm run desk:built
```

This builds the frontend, then serves its immutable assets and API together on
`http://127.0.0.1:4173`. It still binds only to loopback. It does not expose the
Desk to a LAN, tailnet or internet and adds no authentication by itself.

When both gitignored local files exist, the Desk discovers them automatically:

- `runtime/permissions.json`
- `runtime/broker/tasks.sqlite`

Then a topic entered in “What would you like to work on?” is saved as an
immutable Studio proposal. The deterministic Inbox router validates its exact
bytes, records a linked handoff, completes the Inbox task and queues Research.
Research creates candidate evidence, Marketing creates a draft, and Mavery QA checks
the exact draft before the existing human Review queue opens. Without these files
the idea remains a local draft and the UI says automation is not connected. Inbox
does not call a model and no stage can publish.

Setting `autostart: true` for Research, Marketing and Mavery QA in the private
permission file starts one serial local lane. It uses the installed Codex CLI and
the user's existing ChatGPT login and removes inherited API keys. Only Research gets
read-only public web search. Marketing and Mavery QA get no web or shell tools.
Every handoff binds exact file hashes. Mavery QA places the original draft plus its
research and QA evidence in the existing Review queue. Any `false` keeps automation
off. Restarting the Desk resumes up to 20 queued pipeline tasks. Running tasks with
an expired lease are not replayed automatically.

Normal mode is API-only and fails visibly when the API is unavailable. Sample
data is never a silent fallback. To inspect the explicit, visibly labeled demo:

```text
http://127.0.0.1:4173/?demo=1
```

Alternatively set `VITE_REVIEW_DATA_MODE=demo` before starting Vite.

## API boundary

- `GET /api/projects`
- `GET /api/project-desk?project_id=<id>`
- `POST /api/project-briefs`
- `GET /api/reviews?project_id=<id>`
- `GET /api/evidence?project_id=<id>&ref=<project-state-ref>&sha256=<expected-sha256>`
- `POST /api/review-actions`
- `GET /api/studio?project_id=<id>`
- `POST /api/studio`
- `GET /api/broker-status?project_id=<id>`

Every action pins the exact:

- `project_id`
- `project_profile_revision`
- `artifact_id`
- `artifact_revision`
- `artifact_sha256`

`approve` has no note or reason. `decline` includes `reason`. `note` includes
`note` and requests a new revision while leaving the current artifact immutable.
The resulting item moves into the visible **Rework** queue.

`GET /api/project-desk` is a customer-safe read model, intentionally limited to
project goal, audience, success signal, non-goals, readiness steps, blockers,
next roles, active research and optional verified Foundation references. Foundation
documents are deliberately readable, SHA-verified Markdown; their internal paths
and hashes are not returned. Execution logs and raw technical artifacts remain
excluded. When an older local service returns
`404` for this endpoint, the UI renders an explicitly labeled local setup
outline; other service errors remain visible.

`POST /api/project-briefs` appears only while a project needs its first brief.
It accepts the operator's goal, audience, success signal, explicit baseline
status, decision horizon, resources, do-nothing option, non-goals, and research
question, then returns a refreshed customer-safe desk. A `not_measured` baseline
remains visible as an analytics blocker; the desk never fabricates a number. The
request never creates a route, final product, approval, publication, or public
action.

Each review response must include `review_item_version: "1.0"` and an
`artifact_content` string. In normal API mode, `artifact_content` is the final
product bound to the operator's action. The client derives a customer-readable
summary and sections from those exact bytes, while keeping raw JSON envelopes,
paths, hashes, and code out of the review surface. `preview` only frames what the
operator is deciding; it never replaces the selected product. The client rejects
cross-project, cross-revision, and hash-mismatched responses before enabling an
action.

Action progress, success, and errors are shown in a restrained visible status
notice as well as through accessible live-region semantics.

Review responses pin each evidence reference to the SHA-256 of its current exact
bytes. The evidence viewer sends that ref and hash back to the loopback-only API.
The API reads only below the selected project's `evidence/` state tree, rejects
absolute, traversing, cross-project, and symlinked paths, then verifies the bytes
before returning strict UTF-8 text. The customer view presents a readable source
summary rather than raw source bytes or technical identifiers. Other media fail
closed with `render_adapter_required`; they are never rendered from an unverified
preview.

## Checks

```bash
npm run typecheck
npm test
npm run build
```

The accepted visual references are preserved in [`design/`](./design/).
