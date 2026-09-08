# Postiz connector v1

This optional connector sends one exact, human-approved UTF-8 release and its exact
review-bound media to one configured Postiz integration. It is not part of the local
read API and is never started merely by opening Project Desk. The approval endpoint
may queue one durable publisher task only after the terminal action, credential-free
release package and immutable delivery request are stored. That worker invokes the
connector outside the HTTP request.

## Permission intersection

Every request re-verifies the immutable release package, current local permissions,
connector mode and selected integration. `publish = off` denies everything.
`publish = review` permits Postiz drafts only. Scheduled or immediate publication
requires `publish = automatic` as well as a connector mode that permits that action.
The Publisher and Postiz connector must both be enabled.

Project Desk derives the destination platform from the immutable Studio proposal.
In `review` mode approval creates a draft. In `automatic` mode a future planning
slot schedules, a due slot submits immediately, and a proposal without a slot stays
a draft. Connector mode remains a second, narrower gate and may reject the action.

The API key is read only from `POSTIZ_API_KEY`. It is never accepted as a CLI
argument, written to project state, logged or returned. Integration IDs and
provider-specific settings live in gitignored `runtime/postiz.json`; receipts retain
only a hash of the integration ID.

## Duplicate and outage boundary

Postiz post creation has no documented idempotency-key field. The connector writes
an immutable intent before making the HTTP request. If the request or receipt write
has an uncertain outcome, a repeat finds the intent and stops with
`reconciliation required`; it never creates a possible duplicate blindly. A verified
receipt makes exact replay local and causes no second request.

Before creating the post, the connector resolves the immutable material packet from
the approved review manifest and rechecks every Studio record, receipt and byte hash.
It uploads only JPEG, PNG, WebP and MP4 through Postiz's documented multipart upload
endpoint. Text material remains source context. Audio, MOV, WebM and other formats
stop for local conversion instead of being silently dropped.

The intent binds the ordered media hashes before any upload. Therefore any ambiguous
media-upload or post-creation outcome stops for reconciliation instead of retrying a
possibly completed side effect. A `submitted` receipt for `now` proves API acceptance,
not that the platform rendered the post successfully; native platform readback is the
separate live-proof lane.
