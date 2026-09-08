import json
from unittest.mock import patch
from pathlib import Path
import tempfile
import unittest

from pipeline.task_broker import BrokerError, TaskBroker


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.permissions = self.root / 'permissions.json'
        self.config = {'publish': 'review', 'agents': {'writer': {'browser': 'none', 'credentials': 'none'}}}
        self.permissions.write_text(json.dumps(self.config))
        self.now = 100
        self.broker = self.open()

    def open(self):
        broker = TaskBroker(self.root / 'broker.sqlite', self.permissions, clock=lambda: self.now)
        self.addCleanup(broker.close)
        return broker

    def admit(self, **overrides):
        values = dict(project='example', origin='operator', origin_key='message1',
                      agent='writer', input_ref='input/brief.md', input_sha256='a' * 64,
                      actor='local-operator', not_before=100)
        values.update(overrides)
        return self.broker.admit(**values)

    def test_replay_survives_second_connection(self):
        task = self.admit()
        second = self.open()
        self.assertEqual(second.get('example', task['task_id']), self.admit())
        self.assertEqual(second.db.execute('SELECT count(*) FROM audit_events').fetchone()[0], 1)
        with self.assertRaises(BrokerError):
            self.admit(input_sha256='b' * 64)

    def test_claim_has_single_owner(self):
        task = self.admit()['task_id']
        claim = self.broker.claim('example', task, agent='writer')
        with self.assertRaises(BrokerError):
            self.open().claim('example', task, agent='writer')
        self.assertEqual(claim['attempt'], 1)

    def test_revocation_blocks_claim(self):
        task = self.admit()['task_id']
        self.config['agents']['writer']['enabled'] = False
        self.permissions.write_text(json.dumps(self.config))
        with self.assertRaises(BrokerError):
            self.broker.claim('example', task, agent='writer')
        self.assertEqual(self.broker.get('example', task)['status'], 'queued')

    def test_expired_worker_cannot_return(self):
        task = self.admit()['task_id']
        claim = self.broker.claim('example', task, agent='writer', lease_seconds=10)
        self.now = 110
        with self.assertRaises(BrokerError):
            self.broker.fail('example', task, agent='writer', token=claim['lease_token'], version=claim['version'])

    def test_failure_is_atomic_and_terminal(self):
        task = self.admit()['task_id']
        claim = self.broker.claim('example', task, agent='writer')
        result = self.broker.fail('example', task, agent='writer', token=claim['lease_token'], version=claim['version'])
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(result['lease_token'])
        with self.assertRaises(BrokerError):
            self.broker.claim('example', task, agent='writer')

    def test_delegation_atomically_completes_parent_and_admits_child(self):
        self.config['agents'].update({
            'inbox': {'browser': 'none', 'credentials': 'none'},
            'marketing': {'browser': 'none', 'credentials': 'none'},
        })
        self.permissions.write_text(json.dumps(self.config))
        parent = self.admit(agent='inbox')['task_id']
        claim = self.broker.claim('example', parent, agent='inbox')
        values = dict(agent='inbox', token=claim['lease_token'], version=claim['version'],
                      child_agent='marketing', origin_key='handoff-1',
                      input_ref='handoffs/one.json', input_sha256='b' * 64)
        result = self.broker.delegate('example', parent, **values)
        self.assertEqual(result['parent']['status'], 'completed')
        self.assertEqual(result['child']['status'], 'queued')
        self.assertEqual(result['child']['parent_task_id'], parent)
        self.assertEqual(self.broker.delegate('example', parent, **values), result)
        self.assertEqual(self.broker.db.execute(
            'SELECT count(*) FROM tasks').fetchone()[0], 2)
        self.assertEqual(self.broker.db.execute(
            "SELECT count(*) FROM audit_events WHERE event_type='delegated'").fetchone()[0], 1)

    def test_delegation_rolls_back_child_and_parent_when_audit_fails(self):
        self.config['agents'].update({
            'inbox': {'browser': 'none', 'credentials': 'none'},
            'marketing': {'browser': 'none', 'credentials': 'none'},
        })
        self.permissions.write_text(json.dumps(self.config))
        parent = self.admit(agent='inbox')['task_id']
        claim = self.broker.claim('example', parent, agent='inbox')
        with patch.object(self.broker, 'event', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.broker.delegate(
                    'example', parent, agent='inbox', token=claim['lease_token'],
                    version=claim['version'], child_agent='marketing',
                    origin_key='handoff-1', input_ref='handoffs/one.json',
                    input_sha256='b' * 64)
        self.assertEqual(self.broker.get('example', parent)['status'], 'running')
        self.assertEqual(self.broker.db.execute(
            'SELECT count(*) FROM tasks WHERE parent_task_id=?', (parent,)).fetchone()[0], 0)

    def test_expiry_fences_results_without_retry_and_is_idempotent(self):
        task, request = self.result_request()
        self.now = 400
        self.assertEqual(self.open().expire_leases('example', actor='broker'), [task])
        result = self.broker.get('example', task)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['attempt'], 1)
        self.assertEqual(result['version'], request['version'] + 1)
        self.assertIsNone(result['lease_token'])
        self.assertEqual(self.broker.expire_leases('example', actor='broker'), [])
        with self.assertRaises(BrokerError):
            self.broker.accept_verified_result('example', task, **request)
        with self.assertRaises(BrokerError):
            self.broker.claim('example', task, agent='writer')
        events = self.broker.db.execute(
            "SELECT details_json FROM audit_events WHERE event_type='lease_expired'").fetchall()
        self.assertEqual(len(events), 1)
        self.assertNotIn(request['token'], events[0][0])
        self.assertFalse(json.loads(events[0][0])['replay'])

    def test_expiry_is_bounded_and_project_scoped(self):
        for key in ('a', 'b'):
            task = self.admit(origin_key=key)['task_id']
            self.broker.claim('example', task, agent='writer', lease_seconds=10)
        other = self.admit(project='other')['task_id']
        self.broker.claim('other', other, agent='writer', lease_seconds=10)
        live = self.admit(origin_key='live')['task_id']
        self.broker.claim('example', live, agent='writer', lease_seconds=20)
        self.now = 109
        self.assertEqual(self.broker.expire_leases('example', actor='broker'), [])
        self.now = 110
        self.assertEqual(len(self.broker.expire_leases('example', actor='broker', limit=1)), 1)
        self.assertEqual(len(self.broker.expire_leases('example', actor='broker')), 1)
        self.assertEqual(self.broker.get('other', other)['status'], 'running')
        self.assertEqual(self.broker.get('example', live)['status'], 'running')

    def test_expiry_rolls_back_on_audit_failure(self):
        task, _ = self.result_request()
        self.now = 400
        with patch.object(self.broker, 'event', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.broker.expire_leases('example', actor='broker')
        self.assertEqual(self.broker.get('example', task)['status'], 'running')

    def test_expiry_does_not_touch_accepted_results(self):
        task, request = self.result_request()
        self.broker.accept_verified_result('example', task, **request)
        self.now = 1000
        self.assertEqual(self.broker.expire_leases('example', actor='broker'), [])
        self.assertEqual(self.broker.get('example', task)['status'], 'awaiting_review')
        for limit in (True, 0, 101):
            with self.assertRaises(BrokerError):
                self.broker.expire_leases('example', actor='broker', limit=limit)

    def result_request(self):
        task = self.admit()['task_id']
        claim = self.broker.claim('example', task, agent='writer')
        return task, dict(agent='writer', token=claim['lease_token'], version=claim['version'],
                          assets=[dict(asset_id='article', revision='r1', sha256='b' * 64,
                                       artifact_ref='content/article.md', media_type='text/markdown', byte_size=42)])

    def test_result_requires_review_and_replays_without_duplicates(self):
        task, request = self.result_request()
        result = self.broker.accept_verified_result('example', task, **request)
        self.assertEqual(result['status'], 'awaiting_review')
        self.assertIsNone(result['lease_token'])
        self.now = 1000
        self.assertEqual(self.open().accept_verified_result('example', task, **request), result)
        self.assertEqual(self.broker.db.execute('SELECT count(*) FROM task_assets').fetchone()[0], 1)
        self.assertEqual(self.broker.db.execute('SELECT count(*) FROM decisions').fetchone()[0], 0)
        audit = self.broker.db.execute("SELECT details_json FROM audit_events WHERE event_type='result_accepted'").fetchone()[0]
        self.assertNotIn(request['token'], audit)
        request['assets'][0]['sha256'] = 'c' * 64
        with self.assertRaises(BrokerError):
            self.broker.accept_verified_result('example', task, **request)

    def test_result_rejects_invalid_lease_project_agent_and_revocation(self):
        task, request = self.result_request()
        for override in ({'token': 'wrong'}, {'version': 0}, {'agent': 'other'}, {'version': True}):
            with self.subTest(override=override), self.assertRaises(BrokerError):
                self.broker.accept_verified_result('example', task, **(request | override))
        with self.assertRaises(BrokerError):
            self.broker.accept_verified_result('other-project', task, **request)
        self.now = 400
        with self.assertRaises(BrokerError):
            self.broker.accept_verified_result('example', task, **request)
        self.now = 101
        self.config['agents']['writer']['enabled'] = False
        self.permissions.write_text(json.dumps(self.config))
        with self.assertRaises(BrokerError):
            self.broker.accept_verified_result('example', task, **request)
        self.assertEqual(self.broker.db.execute('SELECT count(*) FROM task_assets').fetchone()[0], 0)

    def test_result_rolls_back_assets_and_status_when_audit_fails(self):
        task, request = self.result_request()
        with patch.object(self.broker, 'event', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.broker.accept_verified_result('example', task, **request)
        self.assertEqual(self.broker.get('example', task)['status'], 'running')
        self.assertEqual(self.broker.db.execute('SELECT count(*) FROM task_assets').fetchone()[0], 0)
        self.broker.accept_verified_result('example', task, **request)

    def test_result_rejects_empty_duplicate_and_invalid_descriptors(self):
        task, request = self.result_request()
        asset = request['assets'][0]
        for assets in ([], [asset, asset], [asset | {'byte_size': True}],
                       [asset | {'sha256': 'wrong'}], [asset | {'unknown': 'field'}]):
            with self.subTest(assets=assets), self.assertRaises(BrokerError):
                self.broker.accept_verified_result('example', task, **(request | {'assets': assets}))
        self.assertEqual(self.broker.get('example', task)['status'], 'running')

    def test_malformed_permission_grant_is_denied(self):
        self.config['agents']['writer'] = []
        self.permissions.write_text(json.dumps(self.config))
        with self.assertRaises(BrokerError):
            self.admit()

    def test_read_only_browser_grant_is_snapshotted_but_credentials_stay_denied(self):
        self.config['agents']['research'] = {
            'browser': 'read-only', 'credentials': 'none'}
        self.permissions.write_text(json.dumps(self.config))
        task = self.admit(agent='research', origin_key='research')
        snapshot = self.broker.db.execute(
            'SELECT permissions_json FROM permission_snapshots WHERE project_id=? AND snapshot_id=?',
            ('example', task['permission_snapshot_id'])).fetchone()[0]
        self.assertEqual(json.loads(snapshot), {
            'agent_id': 'research', 'browser': 'read-only', 'credentials': 'none',
            'publish_mode': 'review'})
        self.config['agents']['research']['credentials'] = 'browser-session'
        self.permissions.write_text(json.dumps(self.config))
        with self.assertRaises(BrokerError):
            self.admit(agent='research', origin_key='research-2')

    def test_publisher_uses_only_connector_credentials_and_completes_with_receipt(self):
        self.config['agents']['publisher'] = {
            'enabled': True, 'browser': 'none',
            'credentials': 'per-channel-connector-only',
        }
        self.permissions.write_text(json.dumps(self.config))
        task = self.admit(agent='publisher', origin_key='publisher')['task_id']
        claim = self.broker.claim('example', task, agent='publisher')
        asset = {
            'asset_id': 'delivery-1', 'revision': 'r1', 'sha256': 'b' * 64,
            'artifact_ref': 'records/publisher-receipts/delivery-1.json',
            'media_type': 'application/json', 'byte_size': 42,
        }
        completed = self.broker.accept_verified_completion(
            'example', task, agent='publisher', token=claim['lease_token'],
            version=claim['version'], asset=asset)
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual(self.broker.db.execute(
            'SELECT artifact_ref FROM task_assets WHERE task_id=?',
            (task,)).fetchone()[0], asset['artifact_ref'])
        details = self.broker.db.execute(
            "SELECT details_json FROM audit_events WHERE task_id=? "
            "AND event_type='completion_accepted'", (task,)).fetchone()[0]
        self.assertNotIn(claim['lease_token'], details)

        self.config['agents']['publisher']['credentials'] = 'none'
        self.permissions.write_text(json.dumps(self.config))
        with self.assertRaises(BrokerError):
            self.admit(agent='publisher', origin_key='publisher-2')
