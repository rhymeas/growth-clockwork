from __future__ import annotations

import hashlib
import json
import unittest
from unittest.mock import patch

from pipeline.inbox_worker import route_task
from pipeline.research_worker import ResearchError, run_research_task
from pipeline.tests.test_inbox_worker import InboxWorkerTests


class ResearchWorkerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = InboxWorkerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        parent, _ = self.fixture.proposal_task()
        self.routed = route_task(
            self.fixture.broker, project='alpha', task_id=parent['task_id'],
            workspace=self.fixture.workspace,
            profile_path=self.fixture.profile_path)

    def run_research(self, report='## Finding\n\nPrimary source: https://example.org'):
        with patch('pipeline.research_worker.codex_worker.generate', return_value=report) as worker:
            result = run_research_task(
                self.fixture.broker, project='alpha',
                task_id=self.routed['research_task_id'],
                workspace=self.fixture.workspace, profile_path=self.fixture.profile_path,
                executable='/usr/bin/true')
        return result, worker

    def test_read_only_research_writes_evidence_and_delegates_to_marketing(self):
        result, worker = self.run_research()
        self.assertEqual(result['research_status'], 'completed')
        self.assertEqual(result['next_agent'], 'marketing')
        self.assertEqual(result['next_status'], 'queued')
        self.assertTrue(worker.call_args.kwargs['allow_web'])
        self.assertIn('public web', worker.call_args.args[0])
        report_path = self.fixture.profile_path.parent / 'state' / result['research_ref']
        self.assertEqual(hashlib.sha256(report_path.read_bytes()).hexdigest(),
                         result['research_sha256'])
        self.assertIn('not approval', report_path.read_text())
        child = self.fixture.broker.get('alpha', result['next_task_id'])
        self.assertEqual(child['parent_task_id'], result['research_task_id'])
        handoff = json.loads((self.fixture.profile_path.parent / 'state'
                              / result['handoff_ref']).read_text())
        self.assertEqual(handoff['research']['artifact_sha256'], result['research_sha256'])
        self.assertEqual(handoff['research']['status'], 'candidate_evidence')

    def test_changed_inbox_handoff_is_rejected_before_model(self):
        task = self.fixture.broker.get('alpha', self.routed['research_task_id'])
        path = self.fixture.profile_path.parent / 'state' / task['input_ref']
        path.write_text(path.read_text().replace('Teach source checking', 'Changed'))
        with patch('pipeline.research_worker.codex_worker.generate') as worker, \
                self.assertRaisesRegex(ResearchError, 'hash changed'):
            run_research_task(
                self.fixture.broker, project='alpha', task_id=task['task_id'],
                workspace=self.fixture.workspace, profile_path=self.fixture.profile_path,
                executable='/usr/bin/true')
        worker.assert_not_called()

    def test_browser_permission_is_required_before_claim_or_model(self):
        self.fixture.config['agents']['research']['browser'] = 'none'
        self.fixture.permissions.write_text(json.dumps(self.fixture.config))
        with patch('pipeline.research_worker.codex_worker.generate') as worker, \
                self.assertRaisesRegex(ResearchError, 'read-only browser'):
            run_research_task(
                self.fixture.broker, project='alpha',
                task_id=self.routed['research_task_id'],
                workspace=self.fixture.workspace, profile_path=self.fixture.profile_path,
                executable='/usr/bin/true')
        worker.assert_not_called()
        self.assertEqual(self.fixture.broker.get(
            'alpha', self.routed['research_task_id'])['status'], 'queued')

    def test_model_failure_marks_research_failed_without_child(self):
        with patch('pipeline.research_worker.codex_worker.generate',
                   side_effect=RuntimeError('provider detail')):
            with self.assertRaisesRegex(RuntimeError, 'provider detail'):
                run_research_task(
                    self.fixture.broker, project='alpha',
                    task_id=self.routed['research_task_id'],
                    workspace=self.fixture.workspace,
                    profile_path=self.fixture.profile_path,
                    executable='/usr/bin/true')
        self.assertEqual(self.fixture.broker.get(
            'alpha', self.routed['research_task_id'])['status'], 'failed')
        self.assertEqual(self.fixture.broker.db.execute(
            'SELECT count(*) FROM tasks WHERE parent_task_id=?',
            (self.routed['research_task_id'],)).fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
