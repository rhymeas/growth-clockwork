# Synthetic example context pack

This package contains no real product, customer, market, metric, account, or
publication data. It exists only to prove that an independently selected project
can use the same eight context sections without reading another profile's files
or state.

`manifests/CTX-EXAMPLE-0001.fixture-r1.json` is `ready` only for a runtime that
explicitly selects fixture mode. Every section is marked `synthetic: true`,
`fixture_only: true`, and `publishable: false`. It must never authorize a live,
public, paid, account-changing, or otherwise external action.

Do not overwrite a manifest or section file. A change creates a new revision and
new SHA-256 references. There is intentionally no `latest` alias.

The JSON schemas validate portable shapes only. A runtime resolver must still:

1. resolve every manifest and source reference below this selected profile root;
2. reject absolute paths, `..`, symlinks, and references into another profile;
3. verify the exact bytes against every declared SHA-256;
4. require matching project ID, profile revision, section kind, and revision; and
5. reject this `ready` manifest unless the run itself is explicitly fixture-only.

The resolver is control-plane work and is not implemented by these static files.
