# Task broker v1

Status: executable schema contract with an explicitly bootstrapped local broker.
`pipeline/task_broker.py` implements admission, claim, delegation, failure,
verified-result storage and review reconciliation. It remains an internal service,
not a public or worker-owned database endpoint.
Schema: `task-broker-v1.sql`. Python's bundled SQLite with JSON functions is sufficient;
the contract tests exercise the installed version. IDs and agent names are opaque,
project-neutral values. Timestamps are UTC Unix seconds.

## Ownership

One local broker service owns writes. Gateway, cron, browser collectors and workers
submit requests through that service; none receives the database file or direct SQL
access. Separate folders alone do not enforce this boundary. OS/process permissions
and a constrained service interface must enforce it during integration.

The broker owns task delivery and history. Existing artifact files and their Writer
receipts remain authoritative for content. Store references, not duplicate content,
in this database. Gateway session history is conversational context, not task state.

## Tables

| Table | Purpose |
| --- | --- |
| `permission_snapshots` | Immutable safe projection of the permissions recorded when a task was admitted. No secrets. |
| `tasks` | Source identity, input hash/reference, assigned agent, due time, attempts and current status. |
| `task_assets` | Exact output revision, byte hash and reference, bound to its project and task. |
| `decisions` | One human decision on one exact output revision. Notes require a reference. |
| `audit_events` | Append-only, ordered task events with the actor and safe structured details. |

`broker_schema` identifies v1. Apply the SQL only to an empty database. Never replay
it as an upgrade or suppress existing-table errors. The adapter must check the
version on startup. Enable foreign keys, recursive triggers and a bounded busy timeout
on every connection. Never use `INSERT OR REPLACE`: replay requires explicit readback
and hash comparison. Recursive triggers also prevent replace from deleting immutable rows.

## Admission and replay

All sources use the same admission endpoint, including cron and browser results.
The unique key is `(project_id, origin, origin_key)`. Use a message ID for messages,
a schedule ID plus intended UTC occurrence for cron, and a parent task plus result
ID for worker/connector output. The broker assigns trusted origin/actor fields;
incoming text cannot nominate itself as an operator or authorize new work.

`pipeline.broker_ingress.admit_event` implements this mapping for trusted local
callers. Raw transport/account/event values are canonically hashed into `origin_key`.
Its exact version-1 envelope requires:

- operator: `transport`, `account_id`, `event_id`;
- connector: `connector_id`, `account_id`, `event_id`;
- schedule: `schedule_id`, integer `scheduled_for` occurrence;
- agent: existing same-project `parent_task_id`, `event_id`.

The caller still owns authentication plus durable, hash-verified creation of
`input_ref`; this mapper does not authenticate, write input files, listen on HTTP
or start workers. Never create its identity from prompt text. OpenClaw's run ID is
attempt identity, not a substitute for the transport event ID.

`persist_and_admit_event` supplies the local durable-input half: it derives an
append-only `records/ingress/<hashed-origin>/r1.txt` path, writes the exact UTF-8
bytes through Root Writer without a plaintext request temp file, verifies the writer
receipt, then admits the task. A broker failure may leave one receipted unreferenced
input; exact retry reuses it and changed bytes fail at the writer. This deliberately
does not claim filesystem-plus-SQLite atomicity. The one-shot stdin process boundary
returns only safe task/input receipt fields and never claims or starts a worker.

On a repeated key, compare the request hash and return the original task only if it
matches. A different hash is a conflict, not an update. Hash normalized structured
input, not raw JSON formatting. Existing grants must authorize scheduled/agent work;
retrieved text is data. Persist admission plus an audit event in one transaction.

## Execution protocol

1. Claim a due `queued` task with `BEGIN IMMEDIATE`, a fresh lease token, an incremented
   attempt and version, and a `running` audit event in the same transaction.
2. Start the worker only after commit. Use a bounded lease and timeout. A result is
   accepted only for the matching project, task, version and unexpired lease token.
3. Verify result files, hashes, reference confinement and existing receipts before
   adding asset rows. Commit assets, final status and audit together. Clear the lease.
4. A crash leaves a lease to expire. Read-only work may retry within `max_attempts`.
   The implemented `expire_leases(project, actor=..., limit=100)` maintenance call
   currently fences expired runs as `failed`, clears their leases and records
   `lease_expired` atomically. It never retries automatically, kills a process or
   reconciles already-written files. Its caller must be trusted; no public endpoint
   or recurring maintenance job is enabled. Safe replay still requires separate
   reconciliation and proof that the previous worker cannot continue writing.
   A connector with an uncertain external effect requires reconciliation using its
   operation ID before any replay. A database lease alone cannot prevent a timed-out
   process from continuing to act; terminate or fence the worker as well.

Allowed transitions:

- `queued -> running | cancelled`
- `running -> awaiting_review | completed | failed | cancelled`
- `running -> queued` only after safe expired-lease recovery and with attempts left
- `awaiting_review -> completed | cancelled`

`TaskBroker.delegate` implements the narrow routing transition used by Inbox,
Research and Marketing. It
requires the parent's current unexpired lease and permission snapshot, checks the
child's current grant, admits exactly one linked child, completes the parent and
records both audit events in one SQLite transaction. Exact replay returns the same
pair; conflicting handoffs fail. This is internal broker code, not an HTTP endpoint.

Terminal tasks do not reopen. A note creates a linked new task/revision; decline
closes the reviewed task as cancelled; approve closes the review as completed.
Completed means the task finished, not that content was published. Future delivery
is a separate connector task. Recurring schedules belong to the gateway; SQLite
stores each admitted occurrence and its `not_before`, not another cron interpreter.

## Decisions and permissions

Only the authenticated local operator endpoint records a decision. SQL verifies
that its target exists and the hash matches; it cannot prove human authorship.
The service verifies the current review state and actor before committing the
decision, task transition and audit event together. Notes are immutable; later work
uses a new revision. A decision ID replay returns the prior record only on exact match.

At execution and immediately before an external action, intersect the task snapshot
with current runtime grants: revocations take effect; newer broader rights do not
silently expand old tasks. Missing/invalid permissions deny the action. `publish`
alone never enables a connector or supplies a credential. Publication in review mode
also requires the exact approved package and destination to be checked by the adapter.

## Proof boundary

SQL enforces project-scoped references, dedup keys, bounded counters, lease shape,
valid hash syntax, exact decision targets and append-only supporting records.
The application must enforce transitions, compare-and-swap versions, lease expiry,
atomic audit writes, permissions, actor identity, path safety and real hash matches.
The tests do not prove those future service behaviors. A database owner can modify
schema or drop triggers: append-only here is not tamper-proof storage.

No scheduler, external connector, model worker or publisher is activated by this
contract. Existing runtime remains the only active execution path. Cutover
must stop the old scheduler from claiming any task also owned by the new broker.

### Current storage implementation

`accept_verified_result` accepts descriptors only from a future trusted adapter
after it verifies confined file paths, actual bytes and existing Writer receipts.
The storage method cannot establish those facts from a hash string. Do not bind
it directly to HTTP or expose it as an agent tool. It checks the current agent
permission projection and active lease, stores assets and moves to review in one
transaction. It never creates decisions or publishes. Exact retries return the
existing task, even after the lease expires; changed submissions are rejected.
Tests currently use synthetic descriptors and prove storage behavior only.

`pipeline/broker_writer.py` supplies the local verification bridge using existing
Root Writer code. Its integration tests use real receipt/file pairs, require the
Writer run ID to match the broker task, and reject altered files and descriptors.
Workspace, profile and Writer request must be selected by the trusted service,
not arbitrary client paths. The service and worker isolation are still outstanding.
The shared Writer lock prevents cooperative writers from racing acceptance; it
cannot prevent the OS account owner from modifying files. Consumers must recheck
the recorded hashes before review or any later use.

The same bridge now provides `reconcile_review`: it mirrors an existing verified,
receipted action into broker decisions and terminal task status. This is an internal
trusted operation, not an approval endpoint. The original Review service retains its
local OS-user authority boundary. Failed mirrors leave that decision intact and can
be retried. Notes currently close the prior task without admitting a linked rework
task; automated rework admission is still unimplemented.

### Current local agent chain

The Desk can now admit one exact Studio proposal into a serial, project-scoped
chain: `Inbox -> Research -> Marketing -> Mavery QA -> human Review`. Each arrow is
an atomic broker delegation with a new child task, permission snapshot, immutable
Root Writer handoff and SHA-256. Research alone receives the `read-only` browser
capability and invokes Codex live web search. Marketing and Mavery QA explicitly run
with web disabled. Mavery QA rechecks exact draft and research bytes, then creates a
review manifest and accepts the draft as an asset of its own task. This does not
approve or publish the content. A future Publisher remains outside this chain.
