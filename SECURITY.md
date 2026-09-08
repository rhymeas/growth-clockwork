# Security

## Supported status

Growth Clockwork is early alpha. Security fixes target the current main development
line. No long-term support window is promised yet.

## Reporting a vulnerability

Do not publish credentials, personal data or exploit details in a public issue. Use
the repository host's private vulnerability-reporting or security-advisory feature.
If that private channel is unavailable, open a public issue containing only a request
for a private contact channel—no sensitive details.

Include the affected revision, impact, minimal reproduction and whether any external
account or local secret may have been exposed. Never include a real secret.

## Trust boundary

- The dashboard and API always bind to loopback. Optional remote access supports
  only an exact HTTPS Tailscale Serve origin plus an allowlisted
  `Tailscale-User-Login`; Funnel, LAN binds and arbitrary proxies are unsupported.
- Runtime permissions and credentials are local and gitignored.
- The SQLite owner can modify local history; receipts are integrity evidence, not
  tamper-proof storage.
- Research has read-only public-web access. Drafting and QA have no browser or shell.
- No publisher ships enabled. Adding one requires destination-scoped OAuth, exact
  revision checks, idempotency and external-effect reconciliation.
- A stored Codex login is a credential. Do not copy it into a repository, container,
  CI runner, prompt or support bundle.

Run `python3 -m pipeline.release_gate . --gitleaks gitleaks` in a clean committed
export before sharing it. The gate verifies package receipts and SBOM alignment,
runs Gitleaks over complete Git history, and rejects common personal-data shapes in
historical text. Its pattern scan still cannot prove that every possible secret or
personal datum is absent.
