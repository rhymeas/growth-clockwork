"""Candidate broker SQL invariants; does not start or migrate a runtime."""
import sqlite3
import unittest
from pathlib import Path


SCHEMA = Path(__file__).resolve().parents[2] / 'contracts/task-broker-v1.sql'


class TaskBrokerSchemaTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript(SCHEMA.read_text())
        self.db.execute('INSERT INTO permission_snapshots VALUES (?, ?, ?, ?, ?)',
                        ('example', 'p1', 'a' * 64, '{"publish":"review"}', 1))
        self.add_task('t1', 'event1')

    def add_task(self, task_id, key, project='example'):
        self.db.execute('''INSERT INTO tasks
            (project_id, task_id, origin, origin_key, request_sha256, agent_id,
             input_ref, permission_snapshot_id, not_before, created_at, updated_at)
            VALUES (?, ?, 'operator', ?, ?, 'research', 'inputs/brief.json', 'p1', 1, 1, 1)''',
            (project, task_id, key, 'b' * 64))

    def add_asset(self):
        self.db.execute('INSERT INTO task_assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        ('example', 't1', 'a1', 'r1', 'c' * 64, 'artifacts/lesson.md', 'text/markdown', 10, 2))

    def test_duplicate_delivery_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_task('t2', 'event1')
        self.assertEqual(self.db.execute('SELECT count(*) FROM tasks').fetchone()[0], 1)

    def test_project_cannot_borrow_permissions(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_task('t2', 'event2', 'other')

    def test_running_requires_a_lease_and_attempt(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE tasks SET status = 'running'")
        self.db.execute("UPDATE tasks SET status='running', attempt=1, lease_token='lease1', lease_expires_at=100")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute('UPDATE tasks SET attempt=2')

    def test_approval_cannot_target_different_bytes(self):
        self.add_asset()
        values = ('example', 'd1', 't1', 'a1', 'r1', 'd' * 64, 'approve', 'operator', None, 3)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute('INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', values)
        values = values[:5] + ('c' * 64,) + values[6:]
        self.db.execute('INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', values)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE decisions SET action='decline'")

    def test_assets_permissions_and_audit_are_append_only(self):
        self.add_asset()
        self.db.execute("INSERT INTO audit_events (project_id, task_id, event_id, event_type, actor_id, details_json, created_at) VALUES ('example','t1','e1','admitted','operator','{}',1)")
        for table in ('permission_snapshots', 'task_assets', 'audit_events'):
            with self.subTest(table=table), self.assertRaises(sqlite3.IntegrityError):
                self.db.execute(f'DELETE FROM {table}')

    def test_replace_cannot_overwrite_a_permission_snapshot(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute('INSERT OR REPLACE INTO permission_snapshots VALUES (?, ?, ?, ?, ?)',
                            ('example', 'p1', 'd' * 64, '{"publish":"automatic"}', 2))

    def test_failed_transaction_does_not_leave_partial_admission(self):
        self.db.commit()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.add_task('t2', 'event2')
            self.db.execute("INSERT INTO audit_events (project_id, task_id, event_id, event_type, actor_id, details_json, created_at) VALUES ('other','t2','e2','admitted','operator','{}',1)")
        except sqlite3.IntegrityError:
            self.db.rollback()
        else:
            self.fail('Cross-project audit unexpectedly accepted')
        self.assertEqual(self.db.execute('SELECT count(*) FROM tasks').fetchone()[0], 1)
