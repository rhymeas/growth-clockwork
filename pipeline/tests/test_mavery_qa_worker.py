from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
import uuid

from pipeline import postiz_connector, release_core, studio
from pipeline.inbox_worker import route_task
from pipeline.marketing_worker import run_marketing_task
from pipeline.mavery_qa_worker import MaveryQAError, run_mavery_qa_task
from pipeline.publisher_worker import run_publisher_task
from pipeline.research_worker import run_research_task
from pipeline.task_broker import TaskBroker
from pipeline.tests.test_inbox_worker import InboxWorkerTests


class MaveryQAWorkerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = InboxWorkerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00\x00\x00\x01\x00\x00\x00\x01" + b"\x08\x06\x00\x00\x00" + b"\x00" * 8
        uploaded = studio.write(
            self.fixture.workspace, self.fixture.profile_path, self.fixture.profile,
            {'project_id': 'alpha', 'project_profile_revision': 'alpha-profile-v1',
             'request_id': str(uuid.uuid4()), 'name': 'frame.png',
             'mime_type': 'image/png',
             'content_base64': base64.b64encode(png).decode('ascii')},
            material=True)
        self.material_id = uploaded['material_id']
        self.material_bytes = png
        parent, saved = self.fixture.proposal_task([self.material_id])
        self.proposal_id = saved['studio']['proposals'][0]['id']
        inbox = route_task(
            self.fixture.broker, project='alpha', task_id=parent['task_id'],
            workspace=self.fixture.workspace, profile_path=self.fixture.profile_path)
        with patch('pipeline.research_worker.codex_worker.generate',
                   return_value='Finding from https://example.org'):
            research = run_research_task(
                self.fixture.broker, project='alpha', task_id=inbox['research_task_id'],
                workspace=self.fixture.workspace, profile_path=self.fixture.profile_path,
                executable='/usr/bin/true')
        with patch('pipeline.marketing_worker._prompt', return_value='Draft safely'), \
                patch('pipeline.marketing_worker.codex_worker.generate',
                      return_value='# Complete useful lesson\n\nA method and its limits.'):
            self.marketing = run_marketing_task(
                self.fixture.broker, project='alpha', task_id=research['next_task_id'],
                workspace=self.fixture.workspace, profile_path=self.fixture.profile_path,
                executable='/usr/bin/true')

    def test_separate_qa_prepares_exact_draft_for_human_review(self):
        with patch('pipeline.mavery_qa_worker._prompt', return_value='Review exact draft'), \
                patch('pipeline.mavery_qa_worker.codex_worker.generate',
                      return_value='No issue found in this limited pass.') as worker:
            result = run_mavery_qa_task(
                self.fixture.broker, project='alpha',
                task_id=self.marketing['next_task_id'],
                workspace=self.fixture.workspace, profile_path=self.fixture.profile_path,
                executable='/usr/bin/true')
        self.assertEqual(result['qa_status'], 'awaiting_review')
        self.assertEqual(worker.call_args.args[0], 'Review exact draft')
        self.assertFalse(worker.call_args.kwargs['allow_web'])
        item = next(item for item in self.fixture.fixture.service.reviews('alpha')['reviews']
                    if item['artifact_id'] == result['qa_task_id'])
        self.assertEqual(item['artifact_content'], '# Complete useful lesson\n\nA method and its limits.')
        self.assertEqual([check['status'] for check in item['quality_checks']],
                         ['warn', 'warn', 'warn'])
        self.assertEqual(len(item['evidence']), 3)
        self.assertIn('Candidate research', item['evidence'][0]['label'])
        material_evidence = item['evidence'][2]
        exact = self.fixture.fixture.service.review_material(
            'alpha', material_evidence['ref'], material_evidence['sha256'],
            self.material_id, hashlib.sha256(self.material_bytes).hexdigest())
        self.assertEqual(base64.b64decode(exact['content_base64']), self.material_bytes)
        self.assertEqual(exact['name'], 'frame.png')
        self.assertEqual(self.fixture.broker.db.execute(
            'SELECT count(*) FROM task_assets WHERE task_id=?',
            (result['qa_task_id'],)).fetchone()[0], 5)

        action = {key: item[key] for key in (
            'project_id', 'project_profile_revision', 'artifact_id',
            'artifact_revision', 'artifact_sha256')}
        action_record, _ = self.fixture.fixture.service.review_action(
            action | {'action': 'approve'})
        self.fixture.fixture.service.broker_database = self.fixture.workspace / 'tasks.sqlite'
        self.fixture.fixture.service.broker_permissions = self.fixture.permissions
        # This narrow broker fixture intentionally omits the portable schema
        # registry; Release Core's full suite covers registered validation.
        with patch.object(release_core, '_validate_schema'):
            automation = self.fixture.fixture.service.finalize_review_action(action_record)
        self.assertEqual(automation['status'], 'packaged')
        self.assertRegex(automation['release_package']['artifact_sha256'], r'^[0-9a-f]{64}$')
        self.assertEqual(self.fixture.broker.get('alpha', result['qa_task_id'])['status'],
                         'completed')

        runtime = self.fixture.workspace / 'runtime'
        runtime.mkdir()
        publisher_permissions = {
            'publish': 'automatic',
            'agents': {
                **self.fixture.config['agents'],
                'publisher': {
                    'enabled': True, 'browser': 'none',
                    'credentials': 'per-channel-connector-only',
                },
            },
            'connectors': {'postiz': {'enabled': True, 'mode': 'automatic'}},
        }
        (runtime / 'permissions.json').write_text(json.dumps(publisher_permissions))
        self.fixture.permissions.write_text(json.dumps(publisher_permissions))
        studio.write(
            self.fixture.workspace, self.fixture.profile_path, self.fixture.profile,
            {'project_id': 'alpha',
             'project_profile_revision': 'alpha-profile-v1',
             'request_id': str(uuid.uuid4()), 'kind': 'slot',
             'payload': {'proposal_id': self.proposal_id,
                         'planned_at': '2099-01-02T03:04:05Z',
                         'timezone': 'UTC'}},
        )
        with patch.object(release_core, '_validate_schema'):
            automation = self.fixture.fixture.service.finalize_review_action(action_record)
        self.assertEqual(automation['delivery_status'], 'queued')
        publisher_task = self.fixture.broker.get(
            'alpha', automation['publisher_task_id'])
        self.assertEqual(publisher_task['agent_id'], 'publisher')
        self.assertEqual(publisher_task['status'], 'queued')

        resumed = threading.Event()

        def publisher_runner(*_args, **kwargs):
            self.assertIsNone(kwargs['executable'])
            resumed.set()

        with patch('pipeline.broker_automation.run_publisher_task',
                   side_effect=publisher_runner):
            restarted = type(self.fixture.fixture.service)(
                self.fixture.workspace,
                broker_database=self.fixture.workspace / 'tasks.sqlite',
                broker_permissions=self.fixture.permissions,
                codex_executable=Path('/missing/codex'))
            try:
                self.assertTrue(resumed.wait(2))
            finally:
                restarted.close()

        (runtime / 'postiz.json').write_text(json.dumps({
            'base_url': 'http://127.0.0.1:5000/api/public/v1',
            'integrations': {'youtube': {
                'enabled': True, 'id': 'private-youtube-integration',
                'provider': 'youtube', 'settings': {'__type': 'youtube'},
            }},
        }))
        (runtime / 'permissions.json').chmod(0o600)
        (runtime / 'postiz.json').chmod(0o600)
        real_deliver = postiz_connector.deliver

        def local_delivery(*args, **kwargs):
            return real_deliver(
                *args, **kwargs,
                transport=lambda _url, _key, _payload: [{
                    'postId': 'post-1',
                    'integration': 'private-youtube-integration',
                }],
                media_transport=lambda _url, _key, _name, _mime, _content: {
                    'id': 'media-1', 'path': 'https://uploads.example/frame.png',
                },
                environment={'POSTIZ_API_KEY': 'test-key'},
            )

        publisher_broker = TaskBroker(
            self.fixture.workspace / 'tasks.sqlite', self.fixture.permissions)
        try:
            with patch.object(release_core, '_validate_schema'), \
                    patch('pipeline.publisher_worker.postiz_connector.deliver',
                          side_effect=local_delivery):
                delivery = run_publisher_task(
                    publisher_broker, project='alpha',
                    task_id=publisher_task['task_id'],
                    workspace=self.fixture.workspace,
                    profile_path=self.fixture.profile_path)
        finally:
            publisher_broker.close()
        self.assertEqual(delivery['delivery_status'], 'scheduled')
        self.assertEqual(self.fixture.broker.get(
            'alpha', publisher_task['task_id'])['status'], 'completed')

    def test_changed_draft_is_rejected_before_qa_model(self):
        path = self.fixture.profile_path.parent / 'state' / self.marketing['draft_ref']
        path.write_text(path.read_text() + '\nChanged')
        with patch('pipeline.mavery_qa_worker.codex_worker.generate') as worker, \
                self.assertRaisesRegex(MaveryQAError, 'draft hash changed'):
            run_mavery_qa_task(
                self.fixture.broker, project='alpha',
                task_id=self.marketing['next_task_id'],
                workspace=self.fixture.workspace, profile_path=self.fixture.profile_path,
                executable='/usr/bin/true')
        worker.assert_not_called()

    def test_qa_browser_permission_is_rejected_before_claim(self):
        self.fixture.config['agents']['mavery-qa']['browser'] = 'read-only'
        self.fixture.permissions.write_text(json.dumps(self.fixture.config))
        with patch('pipeline.mavery_qa_worker.codex_worker.generate') as worker, \
                self.assertRaisesRegex(MaveryQAError, 'must not have browser'):
            run_mavery_qa_task(
                self.fixture.broker, project='alpha',
                task_id=self.marketing['next_task_id'],
                workspace=self.fixture.workspace, profile_path=self.fixture.profile_path,
                executable='/usr/bin/true')
        worker.assert_not_called()

    def test_qa_model_failure_is_visible_and_creates_no_review(self):
        with patch('pipeline.mavery_qa_worker._prompt', return_value='Review safely'), \
                patch('pipeline.mavery_qa_worker.codex_worker.generate',
                      side_effect=RuntimeError('quota')):
            with self.assertRaisesRegex(RuntimeError, 'quota'):
                run_mavery_qa_task(
                    self.fixture.broker, project='alpha',
                    task_id=self.marketing['next_task_id'],
                    workspace=self.fixture.workspace,
                    profile_path=self.fixture.profile_path,
                    executable='/usr/bin/true')
        self.assertEqual(self.fixture.broker.get(
            'alpha', self.marketing['next_task_id'])['status'], 'failed')
        self.assertFalse(any(item['artifact_id'] == self.marketing['next_task_id']
                             for item in self.fixture.fixture.service.reviews('alpha')['reviews']))


if __name__ == '__main__':
    unittest.main()
