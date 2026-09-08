import json
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from pipeline import root_writer
from pipeline.broker_writer import accept_writer_result
from pipeline.task_broker import BrokerError, TaskBroker
from pipeline.codex_worker import run_task
from pipeline.tests import test_root_writer


class BrokerWriterTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_root_writer.RootWriterTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        base = Path(self.fixture.temp.name)
        permissions = base / 'permissions.json'
        permissions.write_text(json.dumps({'publish': 'review', 'agents': {
            'writer': {'browser': 'none', 'credentials': 'none'}}}))
        self.broker = TaskBroker(base / 'broker.sqlite', permissions, clock=lambda: 100)
        self.addCleanup(self.broker.close)
        task = self.broker.admit(project='test-project', origin='operator', origin_key='one',
                                 agent='writer', input_ref='brief.md', input_sha256='a' * 64,
                                 actor='operator', not_before=100)
        task = self.broker.claim('test-project', task['task_id'], agent='writer')
        self.path = self.fixture.request([self.fixture.create_write('drafts/article.md', 'A useful lesson')])
        self.request = json.loads(self.path.read_text())
        self.request['run_id'] = task['task_id']
        self.path.write_text(json.dumps(self.request))
        self.args = dict(workspace=self.fixture.workspace, profile_path=self.fixture.profile,
                         project='test-project', task=task['task_id'], agent='writer',
                         token=task['lease_token'], version=task['version'], request=self.request)

    def write(self):
        return root_writer.apply_request(self.fixture.workspace, self.fixture.profile, self.path)

    def accept(self):
        return accept_writer_result(self.broker, **self.args)

    def assert_empty(self):
        self.assertEqual(self.broker.db.execute('SELECT count(*) FROM task_assets').fetchone()[0], 0)
        self.assertEqual(self.broker.get('test-project', self.args['task'])['status'], 'running')

    def test_real_receipt_and_bytes_enter_review_and_replay(self):
        self.write()
        result = self.accept()
        self.assertEqual(result['status'], 'awaiting_review')
        self.assertEqual(self.accept(), result)
        self.assertEqual(self.broker.db.execute('SELECT count(*) FROM task_assets').fetchone()[0], 1)

    def test_missing_receipt_is_not_a_result(self):
        with self.assertRaises((BrokerError, root_writer.WriterError)):
            self.accept()
        self.assert_empty()

    def test_changed_bytes_and_symlink_are_rejected(self):
        self.write()
        target = self.fixture.workspace / 'projects/test-profile/state/drafts/article.md'
        target.write_text('changed')
        with self.assertRaises(root_writer.WriterError):
            self.accept()
        target.unlink()
        target.symlink_to(self.path)
        with self.assertRaises(root_writer.WriterError):
            self.accept()
        self.assert_empty()

    def test_other_task_or_project_cannot_reuse_receipt(self):
        self.write()
        for key in ('task', 'project'):
            with self.subTest(key=key), self.assertRaises(BrokerError):
                accept_writer_result(self.broker, **(self.args | {key: 'another'}))
        self.assert_empty()

    def test_receipt_cannot_substitute_output_descriptors(self):
        receipt = self.write()
        receipt['writes'][0]['media_type'] = 'text/html'
        relative = root_writer._receipt_relative(root_writer.load_project_profile(self.fixture.profile), self.request)
        path = self.fixture.workspace / 'projects/test-profile/state' / relative
        path.write_text(json.dumps(receipt))
        with self.assertRaises(BrokerError):
            self.accept()
        self.assert_empty()

    def test_dispatcher_connects_claim_generation_writer_and_result(self):
        brief = 'Write a useful lesson'
        task = self.broker.admit(project='test-project', origin='operator', origin_key='dispatch',
                                 agent='writer', input_ref='brief.md',
                                 input_sha256=hashlib.sha256(brief.encode()).hexdigest(),
                                 actor='operator', not_before=100)
        with patch('pipeline.codex_worker.generate', return_value='A useful lesson') as generate:
            result = run_task(self.broker, project='test-project', task=task['task_id'], agent='writer',
                              brief=brief, executable='/usr/bin/true', workspace=self.fixture.workspace,
                              profile_path=self.fixture.profile, output_ref='drafts/dispatched.md')
            generate.assert_called_once()
        self.assertEqual(result['status'], 'awaiting_review')
        self.assertEqual((self.fixture.workspace / 'projects/test-profile/state/drafts/dispatched.md').read_text(),
                         'A useful lesson')

    def test_dispatcher_refuses_brief_substitution_before_model_call(self):
        with patch('pipeline.codex_worker.generate') as generate, self.assertRaises(BrokerError):
            run_task(self.broker, project='test-project', task=self.args['task'], agent='writer',
                     brief='Changed input', executable='/usr/bin/true', workspace=self.fixture.workspace,
                     profile_path=self.fixture.profile, output_ref='drafts/dispatched.md')
        generate.assert_not_called()
