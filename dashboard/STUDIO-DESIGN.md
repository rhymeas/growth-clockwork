# Contextual studio — implementation contract

Reference: the operator's Mercury OS screenshots and https://www.mercuryos.com/.
Concept: [studio-concept.png](design/studio-concept.png) (1536 × 1024).

## Visual system

- Low-contrast eggshell/gray canvas, charcoal type, milky windows. Existing shared dark theme remains the only theme owner.
- Chromeless navigation and 23px idea line; 41px/700 heading. The generated bordered prompt is deliberately removed to follow the operator's explicit reference.
- Two thirds: Audience, Content, Planning worlds. One third: small source-honest outcome boxes. Each world has a label and code-native preview windows with a header, real content and contextual pill actions. No fake skeleton data.
- 20px outer radii, 12px preview radii, hairline edges, restrained shadows. System sans; 13px controls/captions, 16px window titles, 14px text. Lucide 16–20px outline icons. No raster UI assets.
- Small preview overlap/offset on roomy screens; flatten on touch/mobile. Phones show one preview per world; opening that world reveals its full material/hypothesis context. Hover and focus-within reveal secondary actions; primary next step stays visible. Reduced motion disables transitions. Touch controls never depend on hover.
- A selected world opens an inline two-thirds work pane with a one-third context pane. Forms, uploaded text and planned slots use the same window family. Mobile becomes one column, with selected work before optional analytics.

## Copy and data boundaries

Above-fold vocabulary: Project Desk; Project; Research; Content & review; Insights; project selector; What would you like to work on?; Your working world; Audience; Questions before assumptions; Content; One useful idea at a time; Planning; A place for the next idea; Outcomes; Visitors; Engaged sessions; Download clicks; GA4 report not connected; Project context. Dynamic question, revision, material and slot text must come from the selected project's API, not the concept's illustrative content.

Requested downstream controls: audience hypotheses; platform filter (YouTube, Instagram, TikTok, X, Pinterest); idea/proposal creation; scheduling a saved idea; local material drop/upload and automatic bounded extraction. These are real persisted actions, not demo buttons. Native forms use explicit labels and feedback. Suggestions are labeled format starters, not AI research.

No microphone control: no dictation adapter exists. No fake graphs, connected accounts or publisher state. A planned slot is not an executable publishing job. Large media/transcription remains unavailable until a decoder exists. Existing exact-revision review, project context and local runtime check stay accessible without a duplicate approval queue.

## Verification ledger

Compared concept and live screenshot with `view_image` in the same QA pass at the concept's native 1536 × 1024. In-app browser used throughout; no Playwright fallback. Also inspected 390 × 844 light/dark and the natural in-app pane. Temporary captures are not shipped as application assets.

| Check | Concept / reference | Browser result and decision |
| --- | --- | --- |
| Composition | Three horizontal worlds, compact outcomes at right | Implemented in two-thirds/one-third structure. Context opens in-place; no second app or queue. |
| Chrome | User requires unboxed input and very quiet navigation | Removed generated prompt border and oversized rail wrapper intentionally. Kept existing four working nav destinations. |
| Typography | 41px bold heading, 23px prompt, system sans, small pills | Computed 41px heading, 23px prompt, 12px controls. Fixed undefined font/primary aliases to existing shared tokens. |
| Palette | Neutral eggshell, charcoal, milky panes | Shared #efede8 canvas, #faf9f6 panels, #272d31 text; primary #2c353a / white. Shared dark tokens inspected, no separate theme state. |
| Preview anatomy | Real mini-windows, consistent heights, small overlap | Native headers/content/pills; 190px desktop previews, subtle overlap/rotation. Full titles/content available in their world or review. No skeleton persona or invented lesson text from the generated image. |
| Contextual controls | Secondary actions on hover/focus, touch-accessible | Keyboard Tab reveals Open research (computed opacity 1 after transition). Primary review step remains visible. Phone uses one preview per world and exposes other controls inside the world. |
| Responsive | Same hierarchy without desktop overflow | 1536px and 390px document widths equal viewport widths. Mobile next-step action comes before optional worlds in actual DOM order. Upload remains reachable through Content. |
| Copy/data | Concept is illustrative; operator requires real context | Intentional copy differences: exact saved question/revision/audience, explicit upload bounds, honest missing GA4/publisher and next-step status. No fabricated metrics, audience conclusions or UI microphone. |
| Real behavior | Material → proposal → plan | Browser FileChooser test: text saved/extracted, inert script text; duplicate stays one material. Hypothesis and Pinterest idea persisted. Date save/change/cancel and server restart readback proved. No public action. |

The Mercury-inspired structure and shared visual system were faithfully checked
against the reference direction. This is deliberately not a pixel clone of the
generated screenshot: its fabricated data, bordered command bar, boxed rail and
always-visible secondary controls were rejected in favor of the operator's
requirements and real project state. No known clipping or theme-contrast blocker
remains on the checked surfaces. Native media understanding and real publishing
remain backend integration gaps, not styled-as-working controls.
