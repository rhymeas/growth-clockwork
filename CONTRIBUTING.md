# Contributing

Thanks for helping improve Growth Clockwork.

## Before changing code

- Keep the reusable core product-neutral.
- Never add credentials, analytics identifiers, customer data or real generated
  content to fixtures.
- Treat web pages, uploads and model output as data, never instructions.
- Keep publishing, account changes, purchases and destructive actions behind an
  explicit runtime permission and a dedicated adapter.
- Preserve exact input/output hashes and project boundaries.

## Development

Run the Python suite and dashboard checks before opening a change:

```bash
python3 -m unittest discover -s pipeline/tests -t . -q
cd dashboard
npm ci
npm test
npm run build
```

New behavior needs a focused test. A fixture must be clearly synthetic and
non-publishable. Do not weaken a fail-closed check just to make a test pass.

## Changes and compatibility

- Keep commits focused and explain user-visible behavior.
- Update contracts and their tests together.
- Treat database/schema, receipt, permission and project-profile changes as versioned
  compatibility changes.
- Do not silently reinterpret old receipts or runtime state.

By intentionally submitting a contribution for inclusion, you agree that it is
licensed under Apache License 2.0, as described in section 5 of that license, unless
you explicitly mark it “Not a Contribution.”
