# Growth Clockwork

Public repository: <https://github.com/rhymeas/growth-clockwork>

Growth Clockwork is a local, open-source control room for evidence-first marketing
work. It turns one operator idea into a receipted chain:

```text
Inbox -> Research -> Marketing -> Project QA -> human Review
```

The reusable core is product-neutral. Product facts, audiences, credentials,
analytics identifiers and generated content stay in local project profiles and
runtime state.

## What is included

- Project Desk: a small React dashboard for direction, research, review and insights.
- SQLite task broker with immutable admission, handoff and decision records.
- Root Writer with path confinement, exact hashes and completion receipts.
- Local Codex subscription workers for read-only research, drafting and QA.
- Optional local Tesseract OCR, FFmpeg/FFprobe inspection and whisper.cpp transcription.
- Synthetic Example Project for tests and learning.

No social credential, analytics account or real product data is included. The
optional Postiz connector is inactive by default and requires a separately operated
Postiz service, private integration map, environment API key and explicit local
permissions. Selected JPEG, PNG, WebP and MP4 material can travel through the same
exact human review into that connector; unsupported media stops for conversion.
GA4 remains an optional integration boundary. OpenClaw is not required
or bundled.

## Requirements

- Python 3.11 or newer.
- Node.js 22 or newer and npm.
- Optional: an installed Codex CLI signed in with `codex login` for model-backed work.
- Optional: Tesseract for image OCR; FFmpeg plus whisper.cpp and a local GGML model
  for audio/video transcription. These tools are not bundled or auto-downloaded.

## Local start

```bash
python3 -m pipeline.bootstrap_runtime --workspace .
cd dashboard
npm ci
npm run desk
```

Open `http://127.0.0.1:4173`. The server binds to loopback only. The initial runtime
uses `publish = review`; it has no publisher credentials.

For normal background use after setup, run the built single-process Desk instead
of the Vite development server:

```bash
cd dashboard
npm run desk:built
```

It serves built assets and API together on loopback with restrictive browser
headers. Every request requires a trusted Host.

Optional private remote access uses Tailscale Serve only. The Desk still listens on
`127.0.0.1`; Tailscale terminates HTTPS and proxies it inside the private tailnet.
Copy `runtime/remote-access.example.json` to the gitignored
`runtime/remote-access.json`, set one canonical `https://...ts.net` origin and the
exact allowed Tailscale login names, then restrict the file to the current user.
The server requires that origin plus Tailscale's authenticated identity header.
It does not support Tailscale Funnel, public internet exposure or a LAN bind.
Installing Tailscale, authenticating devices and enabling Serve remain explicit
external setup actions.

Insights shows three compact operating cards for Automation, Access and Publishing.
They are live bounded states, not optimistic setup claims; no path, identity,
origin, credential or raw permission file is returned to the browser.

On macOS, inspect the opt-in login service before installing it:

```bash
python3 -m pipeline.macos_autostart --workspace .
```

After reviewing its exact paths, `--install` writes one user LaunchAgent and loads
it with `launchctl`. It serves the already-built Desk and its existing SQLite broker
after login while the Mac is awake. The broker database and permission file must
already exist and be private to the current user. It does not build, open a remote
port, store credentials or modify publishing permissions. If agent autostart is
enabled, the generated plist also pins the discovered Codex executable; it does not
store a model API key.

For optional Postiz delivery, copy `runtime/postiz.example.json` to the gitignored
`runtime/postiz.json` and configure it only after the separate Postiz service and
channel OAuth connections exist. Keep `publisher.enabled` and
`connectors.postiz.enabled` false until then. The connector CLI is documented in
`contracts/postiz-connector-v1.md`. Once both switches are enabled, an exact approval
creates a release package and queues a durable publisher task. The HTTP request does
not wait for Postiz, and queued delivery resumes after local restart. Review mode
creates a draft; automatic mode follows the proposal's planning slot.
Failed publisher work appears under Insights → Background tasks with a reconciliation
warning. Growth Clockwork does not offer a blind retry when Postiz may already have
accepted the request.

For optional media processing, copy `runtime/media-processing.example.json` to the
gitignored `runtime/media-processing.json`. Original upload bytes remain immutable.
Derived OCR and transcripts stay local, bounded, and untrusted. Missing tools remain
visible as `indexed`; Growth Clockwork never labels signature checking as processing.
Project Desk accepts files up to 64 MiB each, with a 512 MiB project cap. Binary
uploads are stored as project-local, content-addressed blobs; the small receipted
Studio record binds their exact hash and never exposes the internal blob path.
Processing and review are intentionally memory-bounded one file at a time in this
early alpha. This is not a large-media asset manager or video editor.

For optional website analytics, copy `runtime/ga4.example.json` to the gitignored
`runtime/ga4.json`, enter the selected project's numeric GA4 property ID and actual
property timezone, verify the internal-traffic filter, then enable it. Supply a
short-lived read-only OAuth token only in `GROWTH_GA4_ACCESS_TOKEN` and run:

```bash
python3 -m pipeline.ga4_connector --workspace .
```

The connector calls Google's Data API and writes only a hashed property identity,
window and three aggregate values into `runtime/analytics`. It does not create a
property, install the website tag or refresh OAuth credentials. Schedule it only
through a trusted token-refresh boundary; never store the token in the config.
Project Desk reports the next safe setup state—configuration needed, disabled,
authentication needed or ready—without returning the property ID or token.

Without Codex, the dashboard, broker, deterministic routing, fixtures and tests still
work. Model-backed tasks remain queued until the worker is available.

## Verification

```bash
python3 -m unittest discover -s pipeline/tests -t . -q
cd dashboard
npm test
npm run build
```

For an exported candidate:

```bash
python3 -m pipeline.package_audit .
python3 -m pipeline.release_gate . --gitleaks gitleaks
```

The included Gitleaks configuration extends the scanner's standard rules and only
allows deterministic UUID idempotency keys used by tests. Run it against the full
Git history before every public release.

## Security model

Fetched pages, files, analytics, comments and model output are untrusted data. Only
Research gets read-only public-web access. Marketing and QA get no browser, shell or
credentials. An approval is revision-bound; external delivery happens only through
separately enabled runtime permissions and connector credentials. See
[SECURITY.md](SECURITY.md).

## Status

Early alpha. Local-first, macOS-tested, and not an unattended cloud service. If the
computer sleeps, its workers stop. External platforms always require their own OAuth
grants and terms-compliant connectors.

Licensed under Apache-2.0. See [LICENSE](LICENSE) and
[THIRD-PARTY.md](THIRD-PARTY.md).
