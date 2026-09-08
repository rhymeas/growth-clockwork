# One Project Desk

Project Desk is the single operator application. Growth Clockwork is its engine,
not a second dashboard. Run both locally with `npm run desk` from `dashboard/`.

## Navigation and data ownership

| View | Purpose | Authoritative input |
| --- | --- | --- |
| Project | Goal, audience, production progress, foundation | Selected project brief, runtime status, verified references |
| Research | Collected sources, saved analysis, open questions | Existing project-scoped research API |
| Content & review | Final artifacts, evidence, exact-revision decisions | The existing single review queue |
| Insights | Baseline, success signal, production counts, measurement gaps | Same brief and queue; no fabricated analytics |

`?project=<id>&view=project|research|review|insights` addresses each view. Existing
`view=review` links remain valid. Navigation preserves the selected project and
saved theme. Review approval still does not publish or approve product context.

Foundation references use optional `projects/<profile>/foundation.json` with
explicit project/revision identity and SHA-pinned Markdown source paths. The
server verifies exact bytes, bounds reads and rejects unsafe paths. The UI gets
only readable labels and Markdown, never internal paths/hashes. Changed sources
show **Reference refresh needed**, not an old count or approval. Update a pin and
its summary only after inspecting the changed source. This is a reference reader,
not another task queue or an intermediate human scheduling gate.

## Reviewed design integration

Design reference: `claude/project-desk-dashboard-design-vxqnya`, inspected at
`b03313aa21774bd9f22df412437c20f469dd21b3` in `rhymeas/Marketing-OS`.

The legacy `artifact-growth-desk.html` is **not imported, served, or made into
another application here**. Its static approvals, selected feeds, timers and PR
actions are not runtime data. Its remote branch/artifact remains untouched as a
historical reference; withdrawing any separately shared artifact is separate work.

The earlier palette was superseded by the operator's Mercury OS reference.
The current palette and hierarchy live in `src/desk-tokens.css` and `src/worlds.css`:

- Neutral eggshell ground, soft paper modules, restrained charcoal actions.
- Stronger secondary/tertiary contrast than the reference, in both themes.
- One app-level theme owner (`App.tsx`); no competing CSS system-theme override.
- Three working worlds: Understand, Create, Learn. The selected revision opens as
  a reading sheet with its own decision controls and adjacent evidence.
- System sans-serif throughout. No external font service or download.
- Review labels come from actual state: ready, approved, declined or revision
  requested. Navigation selection is not an approval status.

Insights is an honest local readout, not a GA4 connector. Brief-supplied baselines
are labeled as such. Missing analytics remain unavailable; revision counts are
not publication counts or growth. Full automated publishing, analytics setup and
hosting are outside this UI consolidation.

## Contextual worlds — 2026-09-05

Design direction: [Mercury OS](https://www.mercuryos.com/) and the operator's
screenshots. A generated concept was used as a visual target, not as runtime UI.
The implementation intentionally uses actual project state and adds the requested
small growth-metric boxes instead of copying the concept's empty outcomes card.

- One visible next action. Secondary world actions appear on hover or keyboard
  focus. Touch and narrow layouts keep them visible. Below 900px the active world
  is first in both DOM/keyboard and visual order, not just rearranged with CSS.
- Goal, sources, boundaries, preflight, blockers and hand-offs remain under
  `Project context`. Research uncertainties and supporting checks use disclosures.
- `getContextualFlow` is a pure, project/profile-scoped navigation selector.
  Brief setup comes first; pending review and requested rework take precedence
  over generic research blockers. Dispatch-ready is not described as running.
- Rework opens the correct queue filter and displays the recorded note. Notes
  already returned by the API are now normalized with exact project, profile,
  artifact, revision and hash checks. No backend, action contract or policy changed.
- Small Visitors / Engaged sessions / Download clicks boxes are explicitly
  **unavailable placeholders**. No GA4 report endpoint or collector was added.
  They do not show synthetic zeros, trends, time windows or queue counts as growth.

Validation: 65 frontend tests and production build pass. Desktop (1536px), narrow
desktop (1097px) and mobile (390px and 430px) rendering checked; secondary actions reveal on
keyboard focus without losing tab order. Sampled text/button palette contrasts
exceed 4.5:1 in both themes (minimum 4.86:1). Screenshot receipts are local under
`output/ui-contextual-worlds/`; not a public deployment or live-analytics proof.
