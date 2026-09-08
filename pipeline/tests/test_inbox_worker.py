from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

from pipeline import root_writer, studio
from pipeline.inbox_worker import InboxError, route_task
from pipeline.task_broker import BrokerError, TaskBroker
from pipeline.tests import test_review_api


class InboxWorkerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_review_api.ReviewAPITests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.workspace = self.fixture.workspace
        self.profile_path, self.profile = self.fixture.service._selected_profile('alpha')
        profile_value = json.loads(self.profile_path.read_text())
        profile_value['writer']['allowed_roots'].append('evidence/packets')
        self.profile_path.write_text(json.dumps(profile_value))
        self.profile = root_writer.load_project_profile(self.profile_path)
        self.permissions = self.workspace / 'permissions.json'
        self.config = {'publish': 'review', 'agents': {
            'inbox': {'browser': 'none', 'credentials': 'none'},
            'research': {'browser': 'read-only', 'credentials': 'none'},
            'marketing': {'browser': 'none', 'credentials': 'none'},
            'mavery-qa': {'browser': 'none', 'credentials': 'none'},
        }}
        self.permissions.write_text(json.dumps(self.config))
        self.broker = TaskBroker(self.workspace / 'tasks.sqlite', self.permissions,
                                 clock=lambda: 100)
        self.addCleanup(self.broker.close)

    def proposal_task(self, material_ids=None):
        request = {
            'project_id': 'alpha', 'project_profile_revision': 'alpha-profile-v1',
            'request_id': str(uuid.uuid4()), 'kind': 'proposal',
            'payload': {'title': 'Teach source checking', 'channel': 'youtube',
                        'audience_id': None, 'material_ids': material_ids or []},
        }
        saved = studio.write(
            self.workspace, self.profile_path, self.profile, request)
        record = saved['record']
        task = self.broker.admit(
            project='alpha', origin='operator', origin_key=request['request_id'],
            agent='inbox', input_ref=record['artifact_ref'],
            input_sha256=record['artifact_sha256'], actor='local-operator',
            not_before=0)
        return task, saved

    def test_routes_exact_proposal_to_one_linked_research_task(self):
        task, _ = self.proposal_task()
        result = route_task(
            self.broker, project='alpha', task_id=task['task_id'],
            workspace=self.workspace, profile_path=self.profile_path)
        self.assertEqual(result['inbox_status'], 'completed')
        self.assertEqual(result['research_status'], 'queued')
        child = self.broker.get('alpha', result['research_task_id'])
        self.assertEqual(child['agent_id'], 'research')
        self.assertEqual(child['parent_task_id'], task['task_id'])
        handoff_path = self.profile_path.parent / 'state' / result['handoff_ref']
        self.assertEqual(hashlib.sha256(handoff_path.read_bytes()).hexdigest(),
                         result['handoff_sha256'])
        handoff = json.loads(handoff_path.read_text())
        self.assertEqual(handoff['goal'], 'Teach source checking')
        self.assertEqual(handoff['route'], 'content-channel')
        self.assertEqual(handoff['channel'], 'youtube')

    def test_changed_input_is_rejected_before_claim_or_handoff(self):
        task, saved = self.proposal_task()
        path = self.profile_path.parent / 'state' / saved['record']['artifact_ref']
        path.write_text(path.read_text().replace('Teach source checking', 'Changed'))
        with self.assertRaisesRegex(InboxError, 'hash changed'):
            route_task(self.broker, project='alpha', task_id=task['task_id'],
                       workspace=self.workspace, profile_path=self.profile_path)
        self.assertEqual(self.broker.get('alpha', task['task_id'])['status'], 'queued')
        self.assertFalse((self.profile_path.parent / 'state' / 'records/inbox-handoffs').exists())

    def test_missing_research_grant_denies_before_claim_or_write(self):
        task, _ = self.proposal_task()
        del self.config['agents']['research']
        self.permissions.write_text(json.dumps(self.config))
        with self.assertRaisesRegex(BrokerError, 'permission'):
            route_task(self.broker, project='alpha', task_id=task['task_id'],
                       workspace=self.workspace, profile_path=self.profile_path)
        self.assertEqual(self.broker.get('alpha', task['task_id'])['status'], 'queued')
        self.assertFalse((self.profile_path.parent / 'state' / 'records/inbox-handoffs').exists())

    def test_writer_failure_leaves_inbox_task_queued_for_safe_retry(self):
        task, _ = self.proposal_task()
        with patch.object(root_writer, 'apply_request_value',
                          side_effect=root_writer.WriterError('writer unavailable')):
            with self.assertRaises(root_writer.WriterError):
                route_task(self.broker, project='alpha', task_id=task['task_id'],
                           workspace=self.workspace, profile_path=self.profile_path)
        self.assertEqual(self.broker.get('alpha', task['task_id'])['status'], 'queued')
        self.assertEqual(self.broker.db.execute(
            'SELECT count(*) FROM tasks WHERE parent_task_id=?',
            (task['task_id'],)).fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
