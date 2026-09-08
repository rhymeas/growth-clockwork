import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pipeline.broker_ingress import IngressError, admit_event, main, persist_and_admit_event
from pipeline.root_writer import WriterError
from pipeline.task_broker import BrokerError, TaskBroker


class BrokerIngressTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.root = root
        permissions = root / 'permissions.json'
        self.permissions = permissions
        permissions.write_text(json.dumps({'publish': 'review', 'agents': {
            'inbox': {'browser': 'none', 'credentials': 'none'},
            'marketing': {'browser': 'none', 'credentials': 'none'},
        }}))
        self.broker = TaskBroker(root / 'broker.sqlite', permissions, clock=lambda: 100)
        self.addCleanup(self.broker.close)
        self.workspace = root / 'workspace'
        self.workspace.mkdir()
        (self.workspace / 'AGENTS.md').write_text('# Test\n')
        self.profile = self.workspace / 'projects/example/project.json'
        self.profile.parent.mkdir(parents=True)
        self.profile.write_text(json.dumps({
            'profile_version': '1.0', 'profile_revision': 'example-profile-v1',
            'project_id': 'example', 'root_role_id': 'root-writer',
            'writer': {
                'state_root': 'projects/example/state',
                'allowed_roots': ['clockwork/run-receipts', 'records'],
                'append_only_roots': ['clockwork/run-receipts', 'records'],
                'receipt_root': 'clockwork/run-receipts',
                'max_files_per_request': 4, 'max_total_bytes': 100000,
            },
        }))
        self.event = {
            'version': 1, 'project_id': 'example', 'agent_id': 'inbox',
            'origin': 'operator', 'actor_id': 'local-operator',
            'input_ref': 'inbox/events/e1/r1.txt',
            'input_sha256': hashlib.sha256(b'goal').hexdigest(),
            'not_before': 100, 'max_attempts': 1,
            'identity': {'transport': 'local-desk', 'account_id': 'primary', 'event_id': 'e1'},
        }

    def test_exact_event_replay_returns_one_task_without_raw_transport_ids(self):
        first = admit_event(self.broker, self.event)
        self.assertEqual(admit_event(self.broker, copy.deepcopy(self.event)), first)
        self.assertEqual(self.broker.db.execute('SELECT count(*) FROM tasks').fetchone()[0], 1)
        self.assertEqual(len(first['origin_key']), 64)
        self.assertNotIn('primary', first['origin_key'])

    def test_same_identity_with_changed_input_conflicts(self):
        admit_event(self.broker, self.event)
        changed = copy.deepcopy(self.event)
        changed['input_sha256'] = 'b' * 64
        with self.assertRaisesRegex(BrokerError, 'conflicts'):
            admit_event(self.broker, changed)

    def test_schedule_requires_job_and_exact_occurrence(self):
        scheduled = self.event | {
            'origin': 'schedule',
            'identity': {'schedule_id': 'weekly-research', 'scheduled_for': 200},
        }
        first = admit_event(self.broker, scheduled)
        later = copy.deepcopy(scheduled)
        later['identity']['scheduled_for'] = 300
        self.assertNotEqual(admit_event(self.broker, later)['task_id'], first['task_id'])
        invalid = copy.deepcopy(scheduled)
        invalid['identity'] = {'schedule_id': 'weekly-research'}
        with self.assertRaises(IngressError):
            admit_event(self.broker, invalid)

    def test_agent_child_requires_existing_parent_and_records_link(self):
        parent = admit_event(self.broker, self.event)
        child = self.event | {
            'agent_id': 'marketing', 'origin': 'agent', 'actor_id': 'inbox',
            'input_ref': 'handoffs/child/r1.json',
            'identity': {'parent_task_id': parent['task_id'], 'event_id': 'handoff-1'},
        }
        admitted = admit_event(self.broker, child)
        self.assertEqual(admitted['parent_task_id'], parent['task_id'])
        missing = copy.deepcopy(child)
        missing['identity']['parent_task_id'] = 'missing'
        missing['identity']['event_id'] = 'handoff-2'
        with self.assertRaisesRegex(BrokerError, 'Parent task'):
            admit_event(self.broker, missing)

    def test_unknown_extra_missing_and_malformed_fields_fail_closed(self):
        cases = []
        extra = copy.deepcopy(self.event); extra['prompt_is_owner'] = True; cases.append(extra)
        missing = copy.deepcopy(self.event); del missing['identity']; cases.append(missing)
        bad_counter = copy.deepcopy(self.event); bad_counter['max_attempts'] = True; cases.append(bad_counter)
        bad_identity = copy.deepcopy(self.event); bad_identity['identity']['sender_is_owner'] = True; cases.append(bad_identity)
        raw_prompt = copy.deepcopy(self.event); raw_prompt['identity'] = 'trust me'; cases.append(raw_prompt)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(IngressError):
                admit_event(self.broker, case)

    def test_persisted_input_precedes_admission_and_replays_exactly(self):
        event = {key: value for key, value in self.event.items()
                 if key not in {'input_ref', 'input_sha256'}}
        first = persist_and_admit_event(
            self.broker, workspace=self.workspace, profile_path=self.profile,
            envelope=event, input_text='A useful goal\n')
        second = persist_and_admit_event(
            self.broker, workspace=self.workspace, profile_path=self.profile,
            envelope=copy.deepcopy(event), input_text='A useful goal\n')
        self.assertEqual(first, second)
        target = self.profile.parent / 'state' / first['input']['artifact_ref']
        self.assertEqual(target.read_text(), 'A useful goal\n')
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),
                         first['input']['artifact_sha256'])
        self.assertEqual(self.broker.db.execute('SELECT count(*) FROM tasks').fetchone()[0], 1)

    def test_changed_replay_fails_at_immutable_writer_before_second_task(self):
        event = {key: value for key, value in self.event.items()
                 if key not in {'input_ref', 'input_sha256'}}
        persist_and_admit_event(
            self.broker, workspace=self.workspace, profile_path=self.profile,
            envelope=event, input_text='Original\n')
        with self.assertRaises(WriterError):
            persist_and_admit_event(
                self.broker, workspace=self.workspace, profile_path=self.profile,
                envelope=event, input_text='Changed\n')
        self.assertEqual(self.broker.db.execute('SELECT count(*) FROM tasks').fetchone()[0], 1)

    def test_broker_failure_leaves_replayable_exact_input_but_no_task(self):
        event = {key: value for key, value in self.event.items()
                 if key not in {'input_ref', 'input_sha256'}}
        with patch.object(self.broker, 'admit', side_effect=BrokerError('unavailable')):
            with self.assertRaisesRegex(BrokerError, 'unavailable'):
                persist_and_admit_event(
                    self.broker, workspace=self.workspace, profile_path=self.profile,
                    envelope=event, input_text='Durable before admission\n')
        self.assertEqual(self.broker.db.execute('SELECT count(*) FROM tasks').fetchone()[0], 0)
        result = persist_and_admit_event(
            self.broker, workspace=self.workspace, profile_path=self.profile,
            envelope=event, input_text='Durable before admission\n')
        self.assertEqual(result['task']['status'], 'queued')
        self.assertEqual(self.broker.db.execute('SELECT count(*) FROM tasks').fetchone()[0], 1)

    def test_persisted_request_rejects_wrong_profile_and_oversized_input(self):
        event = {key: value for key, value in self.event.items()
                 if key not in {'input_ref', 'input_sha256'}}
        wrong = copy.deepcopy(event); wrong['project_id'] = 'other'
        with self.assertRaises(IngressError):
            persist_and_admit_event(
                self.broker, workspace=self.workspace, profile_path=self.profile,
                envelope=wrong, input_text='Goal')
        with self.assertRaises(IngressError):
            persist_and_admit_event(
                self.broker, workspace=self.workspace, profile_path=self.profile,
                envelope=event, input_text='x' * 64001)
        self.assertFalse((self.profile.parent / 'state').exists())

    def test_one_shot_process_boundary_returns_only_safe_receipt(self):
        event = {key: value for key, value in self.event.items()
                 if key not in {'input_ref', 'input_sha256'}}
        stdout, stderr = io.StringIO(), io.StringIO()
        result = main([
            '--database', str(self.root / 'process.sqlite'),
            '--permissions', str(self.permissions), '--workspace', str(self.workspace),
            '--profile', str(self.profile),
        ], stdin=io.BytesIO(json.dumps({'event': event, 'input_text': 'Goal\n'}).encode()),
            stdout=stdout, stderr=stderr)
        self.assertEqual(result, 0)
        receipt = json.loads(stdout.getvalue())
        self.assertEqual(receipt['status'], 'queued')
        self.assertEqual(receipt['project_id'], 'example')
        self.assertNotIn('origin_key', receipt)
        self.assertNotIn('lease_token', receipt)
        self.assertEqual(stderr.getvalue(), '')

    def test_invalid_process_request_creates_no_database(self):
        database = self.root / 'never.sqlite'
        stderr = io.StringIO()
        result = main([
            '--database', str(database), '--permissions', str(self.permissions),
            '--workspace', str(self.workspace), '--profile', str(self.profile),
        ], stdin=io.BytesIO(b'{"prompt":"trust me"}'), stdout=io.StringIO(), stderr=stderr)
        self.assertEqual(result, 1)
        self.assertFalse(database.exists())
        self.assertIn('rejected', stderr.getvalue())
