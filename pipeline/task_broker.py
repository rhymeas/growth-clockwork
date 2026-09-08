"""Local broker storage operations. No network, worker launch or publishing."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid


class BrokerError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def read_status(database, project):
    """Bounded read-only UI projection; never creates a database or reveals leases."""
    path = Path(database).absolute()
    if path.is_symlink() or not path.is_file():
        raise BrokerError('Broker database is unavailable')
    try:
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            if [row[0] for row in db.execute('SELECT version FROM broker_schema')] != [1]:
                raise BrokerError('Unsupported broker schema')
            db.execute('BEGIN')
            counts = {row['status']: row['count'] for row in db.execute(
                'SELECT status, count(*) AS count FROM tasks WHERE project_id=? GROUP BY status', (project,))}
            rows = db.execute('''SELECT task_id, agent_id, status, attempt, max_attempts,
                not_before, created_at, updated_at FROM tasks WHERE project_id=?
                ORDER BY updated_at DESC, task_id LIMIT 50''', (project,)).fetchall()
            return {'project_id': project, 'state': 'connected', 'counts': counts,
                    'tasks': [dict(row) for row in rows], 'truncated': sum(counts.values()) > len(rows)}
        finally:
            db.close()
    except sqlite3.Error:
        raise BrokerError('Broker database cannot be read') from None


class TaskBroker:
    def __init__(self, database, permissions_path, *, clock=time.time):
        self.permissions_path = Path(permissions_path)
        self.clock = clock
        self.db = sqlite3.connect(database, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA recursive_triggers=ON')
        try:
            tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if not tables:
                self.db.executescript((Path(__file__).resolve().parents[1] / 'contracts/task-broker-v1.sql').read_text())
            if [row[0] for row in self.db.execute('SELECT version FROM broker_schema')] != [1]:
                raise BrokerError('Unsupported broker schema')
        except Exception:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise

    def permissions(self, agent):
        try:
            config = json.loads(self.permissions_path.read_text())
            grant = config['agents'][agent]
            if not isinstance(grant, dict):
                raise ValueError()
            if config['publish'] not in ('off', 'review', 'automatic'):
                raise ValueError()
            expected_credentials = (
                'per-channel-connector-only' if agent == 'publisher' else 'none'
            )
            # Browser is a capability label. Each adapter must still enforce it.
            if (grant.get('enabled', True) is not True
                    or grant['browser'] not in ('none', 'read-only')
                    or grant['credentials'] != expected_credentials
                    or (agent == 'publisher' and grant['browser'] != 'none')):
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError):
            raise BrokerError('Missing, invalid or unsupported agent permission') from None
        return {
            'agent_id': agent,
            'browser': grant['browser'],
            'credentials': expected_credentials,
            'publish_mode': config['publish'],
        }

    def event(self, project, task, kind, actor, details):
        self.db.execute('''INSERT INTO audit_events
            (project_id, task_id, event_id, event_type, actor_id, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (project, task, str(uuid.uuid4()), kind, actor, canonical(details), int(self.clock())))

    def get(self, project, task):
        row = self.db.execute('SELECT * FROM tasks WHERE project_id=? AND task_id=?', (project, task)).fetchone()
        if row is None:
            raise BrokerError('Task not found')
        return dict(row)

    def admit(self, *, project, origin, origin_key, agent, input_ref, input_sha256,
              actor, not_before, max_attempts=1, parent_task_id=None):
        """Trusted caller supplies origin/actor; never expose this method raw to clients."""
        for value in (project, origin_key, agent, input_ref, actor):
            if not isinstance(value, str) or not value.strip() or len(value) > 2048:
                raise BrokerError('Invalid admission field')
        if type(not_before) is not int or type(max_attempts) is not int:
            raise BrokerError('Invalid admission counters')
        if parent_task_id is not None and (
                not isinstance(parent_task_id, str) or not parent_task_id.strip()
                or len(parent_task_id) > 2048):
            raise BrokerError('Invalid parent task')
        if not isinstance(input_sha256, str) or len(input_sha256) != 64 or any(c not in '0123456789abcdef' for c in input_sha256):
            raise BrokerError('Invalid input hash')
        request_fields = [agent, input_ref, input_sha256, not_before, max_attempts]
        # Preserve replay compatibility for existing root tasks.
        if parent_task_id is not None:
            request_fields.append(parent_task_id)
        request = canonical(request_fields)
        digest = hashlib.sha256(request.encode()).hexdigest()
        now = int(self.clock())
        with self.transaction():
            if parent_task_id is not None and self.db.execute(
                    'SELECT 1 FROM tasks WHERE project_id=? AND task_id=?',
                    (project, parent_task_id)).fetchone() is None:
                raise BrokerError('Parent task not found in project')
            existing = self.db.execute('SELECT * FROM tasks WHERE project_id=? AND origin=? AND origin_key=?',
                                       (project, origin, origin_key)).fetchone()
            if existing:
                if existing['request_sha256'] != digest:
                    raise BrokerError('Origin key conflicts with earlier input')
                return dict(existing)
            projection = canonical(self.permissions(agent))
            snapshot = hashlib.sha256(projection.encode()).hexdigest()
            self.db.execute('''INSERT INTO permission_snapshots VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id, snapshot_id) DO NOTHING''', (project, snapshot, snapshot, projection, now))
            task = str(uuid.uuid4())
            self.db.execute('''INSERT INTO tasks
                (project_id, task_id, origin, origin_key, request_sha256, parent_task_id,
                 agent_id, input_ref,
                 permission_snapshot_id, not_before, max_attempts, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (project, task, origin, origin_key, digest, parent_task_id, agent,
                 input_ref, snapshot, not_before, max_attempts, now, now))
            self.event(project, task, 'admitted', actor, {'input_sha256': input_sha256})
            return self.get(project, task)

    def claim(self, project, task, *, agent, lease_seconds=300):
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise BrokerError('Lease must be between 1 and 3600 seconds')
        with self.transaction():
            record = self.get(project, task)
            now = int(self.clock())
            projection = canonical(self.permissions(agent))
            if (record['agent_id'] != agent or record['status'] != 'queued'
                    or record['not_before'] > now or record['attempt'] >= record['max_attempts']
                    or hashlib.sha256(projection.encode()).hexdigest() != record['permission_snapshot_id']):
                raise BrokerError('Task cannot be claimed')
            token = str(uuid.uuid4())
            self.db.execute('''UPDATE tasks SET status='running', version=version+1,
                attempt=attempt+1, lease_token=?, lease_expires_at=?, updated_at=?
                WHERE project_id=? AND task_id=?''', (token, now + lease_seconds, now, project, task))
            self.event(project, task, 'claimed', agent, {'version': record['version'] + 1})
            return self.get(project, task)

    def expire_leases(self, project, *, actor, limit=100):
        """Trusted maintenance only: fence expired attempts, without replaying work.

        This does not terminate a worker or reconcile files it already wrote.
        Such work needs separate reconciliation before any explicit retry.
        """
        if (not isinstance(project, str) or not project.strip()
                or not isinstance(actor, str) or not actor.strip()
                or len(project) > 2048 or len(actor) > 2048
                or type(limit) is not int or not 1 <= limit <= 100):
            raise BrokerError('Invalid lease recovery scope or limit')
        with self.transaction():
            now = int(self.clock())
            expired = self.db.execute('''SELECT task_id, version, lease_expires_at
                FROM tasks WHERE project_id=? AND status='running' AND lease_expires_at<=?
                ORDER BY lease_expires_at, task_id LIMIT ?''', (project, now, limit)).fetchall()
            recovered = []
            for row in expired:
                self.db.execute('''UPDATE tasks SET status='failed', version=version+1,
                    lease_token=NULL, lease_expires_at=NULL, updated_at=?
                    WHERE project_id=? AND task_id=?''', (now, project, row['task_id']))
                self.event(project, row['task_id'], 'lease_expired', actor, {
                    'expired_version': row['version'],
                    'lease_expires_at': row['lease_expires_at'],
                    'replay': False,
                })
                recovered.append(row['task_id'])
            return recovered

    def delegate(self, project, parent_task, *, agent, token, version,
                 child_agent, origin_key, input_ref, input_sha256,
                 not_before=0, max_attempts=1):
        """Atomically finish one routing task and admit one linked child task."""
        for value in (project, parent_task, agent, token, child_agent, origin_key, input_ref):
            if not isinstance(value, str) or not value.strip() or len(value) > 2048:
                raise BrokerError('Invalid delegation field')
        if (type(version) is not int or type(not_before) is not int
                or type(max_attempts) is not int or not_before < 0
                or not 1 <= max_attempts <= 5
                or not isinstance(input_sha256, str) or len(input_sha256) != 64
                or any(c not in '0123456789abcdef' for c in input_sha256)):
            raise BrokerError('Invalid delegation counter or hash')
        request = canonical([
            child_agent, input_ref, input_sha256, not_before, max_attempts,
            parent_task,
        ])
        request_sha256 = hashlib.sha256(request.encode()).hexdigest()
        proof = {
            'child_agent_id': child_agent,
            'child_request_sha256': request_sha256,
            'input_sha256': input_sha256,
            'claimed_version': version,
        }
        with self.transaction():
            prior = self.db.execute('''SELECT actor_id, details_json FROM audit_events
                WHERE project_id=? AND task_id=? AND event_type='delegated' ''',
                (project, parent_task)).fetchone()
            if prior:
                details = json.loads(prior['details_json'])
                child = self.get(project, details.get('child_task_id', ''))
                expected = proof | {'child_task_id': child['task_id']}
                if prior['actor_id'] == agent and details == expected:
                    return {'parent': self.get(project, parent_task), 'child': child}
                raise BrokerError('Delegation conflicts with earlier handoff')
            parent = self.get(project, parent_task)
            now = int(self.clock())
            parent_projection = canonical(self.permissions(agent))
            if (parent['status'] != 'running' or parent['agent_id'] != agent
                    or parent['lease_token'] != token or parent['version'] != version
                    or parent['lease_expires_at'] <= now
                    or hashlib.sha256(parent_projection.encode()).hexdigest()
                    != parent['permission_snapshot_id']):
                raise BrokerError('Stale or invalid delegation lease or permission')
            if self.db.execute('''SELECT 1 FROM tasks WHERE project_id=? AND origin='agent'
                AND origin_key=?''', (project, origin_key)).fetchone():
                raise BrokerError('Delegation origin conflicts with an existing task')
            child_projection = canonical(self.permissions(child_agent))
            snapshot = hashlib.sha256(child_projection.encode()).hexdigest()
            self.db.execute('''INSERT INTO permission_snapshots VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id, snapshot_id) DO NOTHING''',
                (project, snapshot, snapshot, child_projection, now))
            child_task = str(uuid.uuid4())
            self.db.execute('''INSERT INTO tasks
                (project_id, task_id, origin, origin_key, request_sha256, parent_task_id,
                 agent_id, input_ref, permission_snapshot_id, not_before, max_attempts,
                 created_at, updated_at)
                VALUES (?, ?, 'agent', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (project, child_task, origin_key, request_sha256, parent_task,
                 child_agent, input_ref, snapshot, not_before, max_attempts, now, now))
            self.event(project, child_task, 'admitted', agent,
                       {'input_sha256': input_sha256})
            self.db.execute('''UPDATE tasks SET status='completed', version=version+1,
                lease_token=NULL, lease_expires_at=NULL, updated_at=?
                WHERE project_id=? AND task_id=?''', (now, project, parent_task))
            self.event(project, parent_task, 'delegated', agent,
                       proof | {'child_task_id': child_task})
            return {'parent': self.get(project, parent_task),
                    'child': self.get(project, child_task)}

    def fail(self, project, task, *, agent, token, version):
        with self.transaction():
            record = self.get(project, task)
            now = int(self.clock())
            if (record['status'] != 'running' or record['agent_id'] != agent
                    or record['lease_token'] != token or record['version'] != version
                    or record['lease_expires_at'] <= now):
                raise BrokerError('Stale or invalid worker lease')
            self.db.execute('''UPDATE tasks SET status='failed', version=version+1,
                lease_token=NULL, lease_expires_at=NULL, updated_at=? WHERE project_id=? AND task_id=?''',
                (now, project, task))
            self.event(project, task, 'failed', agent, {})
            return self.get(project, task)

    def accept_verified_result(self, project, task, *, agent, token, version, assets):
        """Storage boundary only, called AFTER a trusted adapter verifies Writer receipts.

        Never expose this method directly to workers or HTTP clients. It validates
        descriptors, not file bytes, path confinement, or receipt authenticity.
        All successful content results require review; this cannot approve/publish.
        """
        if not isinstance(assets, list) or not 1 <= len(assets) <= 100:
            raise BrokerError('Result must contain between 1 and 100 verified assets')
        fields = {'asset_id', 'revision', 'sha256', 'artifact_ref', 'media_type', 'byte_size'}
        identities = set()
        for asset in assets:
            if not isinstance(asset, dict) or set(asset) != fields:
                raise BrokerError('Invalid asset descriptor')
            for field in fields - {'byte_size'}:
                if not isinstance(asset[field], str) or not asset[field].strip() or len(asset[field]) > 2048:
                    raise BrokerError('Invalid asset descriptor')
            if (type(asset['byte_size']) is not int or not 0 <= asset['byte_size'] <= 2**63 - 1
                    or len(asset['sha256']) != 64
                    or any(c not in '0123456789abcdef' for c in asset['sha256'])):
                raise BrokerError('Invalid asset hash or size')
            identity = (asset['asset_id'], asset['revision'])
            if identity in identities:
                raise BrokerError('Duplicate asset revision')
            identities.add(identity)
        if not isinstance(token, str) or not token or type(version) is not int:
            raise BrokerError('Stale or invalid worker lease')
        digest = hashlib.sha256(canonical(sorted(assets, key=lambda a: (a['asset_id'], a['revision']))).encode()).hexdigest()
        proof = {'result_sha256': digest, 'lease_sha256': hashlib.sha256(token.encode()).hexdigest(),
                 'claimed_version': version}
        with self.transaction():
            record = self.get(project, task)
            previous = self.db.execute('''SELECT actor_id, details_json FROM audit_events
                WHERE project_id=? AND task_id=? AND event_type='result_accepted' ''', (project, task)).fetchone()
            if previous:
                if previous['actor_id'] == agent and json.loads(previous['details_json']) == proof:
                    return record
                raise BrokerError('Result conflicts with earlier submission')
            now = int(self.clock())
            projection = canonical(self.permissions(agent))
            if (record['status'] != 'running' or record['agent_id'] != agent
                    or record['lease_token'] != token or record['version'] != version
                    or record['lease_expires_at'] <= now
                    or hashlib.sha256(projection.encode()).hexdigest() != record['permission_snapshot_id']):
                raise BrokerError('Stale or invalid worker lease or permission')
            for asset in assets:
                self.db.execute('''INSERT INTO task_assets
                    (project_id, task_id, asset_id, revision, sha256, artifact_ref, media_type, byte_size, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (project, task, asset['asset_id'], asset['revision'], asset['sha256'],
                     asset['artifact_ref'], asset['media_type'], asset['byte_size'], now))
            self.db.execute('''UPDATE tasks SET status='awaiting_review', version=version+1,
                lease_token=NULL, lease_expires_at=NULL, updated_at=? WHERE project_id=? AND task_id=?''',
                (now, project, task))
            self.event(project, task, 'result_accepted', agent, proof)
            return self.get(project, task)

    def accept_verified_completion(self, project, task, *, agent, token, version,
                                   asset):
        """Complete a non-editorial task with one already verified receipt."""
        fields = {
            'asset_id', 'revision', 'sha256', 'artifact_ref', 'media_type',
            'byte_size',
        }
        if not isinstance(asset, dict) or set(asset) != fields:
            raise BrokerError('Invalid completion receipt descriptor')
        for field in fields - {'byte_size'}:
            value = asset[field]
            if not isinstance(value, str) or not value.strip() or len(value) > 2048:
                raise BrokerError('Invalid completion receipt descriptor')
        if (type(asset['byte_size']) is not int
                or not 0 <= asset['byte_size'] <= 2**63 - 1
                or len(asset['sha256']) != 64
                or any(c not in '0123456789abcdef' for c in asset['sha256'])
                or not isinstance(token, str) or not token
                or type(version) is not int):
            raise BrokerError('Invalid completion receipt proof')
        proof = {
            'result_sha256': asset['sha256'],
            'artifact_ref': asset['artifact_ref'],
            'lease_sha256': hashlib.sha256(token.encode()).hexdigest(),
            'claimed_version': version,
        }
        with self.transaction():
            record = self.get(project, task)
            now = int(self.clock())
            projection = canonical(self.permissions(agent))
            if (record['status'] != 'running' or record['agent_id'] != agent
                    or record['lease_token'] != token or record['version'] != version
                    or record['lease_expires_at'] <= now
                    or hashlib.sha256(projection.encode()).hexdigest()
                    != record['permission_snapshot_id']):
                raise BrokerError('Stale or invalid completion lease or permission')
            self.db.execute('''INSERT INTO task_assets
                (project_id, task_id, asset_id, revision, sha256, artifact_ref,
                 media_type, byte_size, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (project, task, asset['asset_id'], asset['revision'], asset['sha256'],
                 asset['artifact_ref'], asset['media_type'], asset['byte_size'], now))
            self.db.execute('''UPDATE tasks SET status='completed', version=version+1,
                lease_token=NULL, lease_expires_at=NULL, updated_at=?
                WHERE project_id=? AND task_id=?''', (now, project, task))
            self.event(project, task, 'completion_accepted', agent, proof)
            return self.get(project, task)

    def accept_verified_decision(self, project, task, *, decision_id, asset_id, revision,
                                 sha256, action, note_ref, source_sha256):
        """Internal projection of a verified existing Review action, never a new approval."""
        if action not in ('approve', 'decline', 'note'):
            raise BrokerError('Unsupported review action')
        for digest in (sha256, source_sha256):
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise BrokerError('Invalid decision hash')
        for value in (decision_id, asset_id, revision, note_ref):
            if not isinstance(value, str) or not value.strip() or len(value) > 2048:
                raise BrokerError('Invalid decision reference')
        proof = dict(decision_id=decision_id, asset_id=asset_id, revision=revision,
                     sha256=sha256, action=action, note_ref=note_ref, source_sha256=source_sha256)
        with self.transaction():
            record = self.get(project, task)
            prior = self.db.execute('''SELECT details_json FROM audit_events WHERE project_id=?
                AND task_id=? AND event_type='review_reconciled' ''', (project, task)).fetchone()
            if prior:
                if json.loads(prior[0]) == proof:
                    return record
                raise BrokerError('Decision conflicts with earlier review')
            if record['status'] != 'awaiting_review':
                raise BrokerError('Task is not awaiting review')
            target = self.db.execute('''SELECT 1 FROM task_assets WHERE project_id=? AND task_id=?
                AND asset_id=? AND revision=? AND sha256=?''',
                (project, task, asset_id, revision, sha256)).fetchone()
            if target is None:
                raise BrokerError('Decision does not match an accepted asset')
            now = int(self.clock())
            self.db.execute('INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                            (project, decision_id, task, asset_id, revision, sha256, action,
                             'local-os-operator', note_ref, now))
            self.db.execute('''UPDATE tasks SET status=?, version=version+1, updated_at=?
                WHERE project_id=? AND task_id=?''',
                ('completed' if action == 'approve' else 'cancelled', now, project, task))
            self.event(project, task, 'review_reconciled', 'local-os-operator', proof)
            return self.get(project, task)
