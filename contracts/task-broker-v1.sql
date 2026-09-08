-- Candidate contract only. No production database is created by this file.
-- Every connection must enable foreign_keys before starting a transaction.
PRAGMA foreign_keys = ON;
PRAGMA recursive_triggers = ON;

BEGIN IMMEDIATE;

CREATE TABLE broker_schema (
    version INTEGER PRIMARY KEY CHECK (version = 1)
);
INSERT INTO broker_schema VALUES (1);

-- Safe projection of permissions only: never credentials or browser sessions.
CREATE TABLE permission_snapshots (
    project_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    permissions_json TEXT NOT NULL CHECK (json_valid(permissions_json)),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    PRIMARY KEY (project_id, snapshot_id)
);

CREATE TABLE tasks (
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN ('operator', 'schedule', 'connector', 'agent')),
    origin_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64 AND request_sha256 NOT GLOB '*[^0-9a-f]*'),
    parent_task_id TEXT,
    agent_id TEXT NOT NULL,
    input_ref TEXT NOT NULL,
    permission_snapshot_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN (
        'queued', 'running', 'awaiting_review', 'completed', 'failed', 'cancelled'
    )),
    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 1 CHECK (max_attempts BETWEEN 1 AND 5),
    not_before INTEGER NOT NULL CHECK (not_before >= 0),
    lease_token TEXT,
    lease_expires_at INTEGER,
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    updated_at INTEGER NOT NULL CHECK (updated_at >= created_at),
    PRIMARY KEY (project_id, task_id),
    UNIQUE (project_id, origin, origin_key),
    FOREIGN KEY (project_id, parent_task_id) REFERENCES tasks(project_id, task_id),
    FOREIGN KEY (project_id, permission_snapshot_id)
        REFERENCES permission_snapshots(project_id, snapshot_id),
    CHECK (parent_task_id IS NULL OR parent_task_id <> task_id),
    CHECK (attempt <= max_attempts),
    CHECK (
        (status = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL
            AND lease_expires_at > updated_at AND attempt > 0)
        OR (status <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)
    )
);
CREATE INDEX ready_tasks ON tasks(status, not_before);

-- References to immutable existing files. Files remain outside SQLite.
CREATE TABLE task_assets (
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    revision TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    artifact_ref TEXT NOT NULL,
    media_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    PRIMARY KEY (project_id, task_id, asset_id, revision, sha256),
    UNIQUE (project_id, task_id, asset_id, revision),
    FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id)
);

CREATE TABLE decisions (
    project_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    revision TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('approve', 'decline', 'note')),
    actor_id TEXT NOT NULL,
    note_ref TEXT,
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    PRIMARY KEY (project_id, decision_id),
    UNIQUE (project_id, task_id, asset_id, revision),
    FOREIGN KEY (project_id, task_id, asset_id, revision, sha256)
        REFERENCES task_assets(project_id, task_id, asset_id, revision, sha256),
    CHECK (action <> 'note' OR note_ref IS NOT NULL)
);

CREATE TABLE audit_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    details_json TEXT NOT NULL CHECK (json_valid(details_json)),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    UNIQUE (project_id, event_id),
    FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id)
);

CREATE TRIGGER permissions_no_update BEFORE UPDATE ON permission_snapshots
BEGIN SELECT RAISE(ABORT, 'permission snapshots are append-only'); END;
CREATE TRIGGER permissions_no_delete BEFORE DELETE ON permission_snapshots
BEGIN SELECT RAISE(ABORT, 'permission snapshots are append-only'); END;
CREATE TRIGGER assets_no_update BEFORE UPDATE ON task_assets
BEGIN SELECT RAISE(ABORT, 'asset revisions are append-only'); END;
CREATE TRIGGER assets_no_delete BEFORE DELETE ON task_assets
BEGIN SELECT RAISE(ABORT, 'asset revisions are append-only'); END;
CREATE TRIGGER decisions_no_update BEFORE UPDATE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;
CREATE TRIGGER decisions_no_delete BEFORE DELETE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;

COMMIT;
