from __future__ import annotations

import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pipeline.inbox_worker import route_task
from pipeline.marketing_worker import MarketingError, _prompt, run_marketing_task
from pipeline.research_worker import run_research_task
from pipeline.tests.test_inbox_worker import InboxWorkerTests


class MarketingWorkerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = InboxWorkerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        parent, _ = self.fixture.proposal_task()
        inbox = route_task(
            self.fixture.broker, project='alpha', task_id=parent['task_id'],
            workspace=self.fixture.workspace,
            profile_path=self.fixture.profile_path)
        with patch('pipeline.research_worker.codex_worker.generate',
                   return_value='## Evidence\n\nSource: https://example.org'):
            self.routed = run_research_task(
                self.fixture.broker, project='alpha',
                task_id=inbox['research_task_id'], workspace=self.fixture.workspace,
                profile_path=self.fixture.profile_path, executable='/usr/bin/true')

    def test_dispatch_verifies_handoff_and_supplies_derived_prompt(self):
        with patch('pipeline.marketing_worker._prompt', return_value='Safe prompt'), \
                patch('pipeline.marketing_worker.codex_worker.generate', return_value='Useful draft') as worker:
            result = run_marketing_task(
                self.fixture.broker, project='alpha',
                task_id=self.routed['next_task_id'],
                workspace=self.fixture.workspace, profile_path=self.fixture.profile_path,
                executable='/usr/bin/true')
        self.assertEqual(result['marketing_status'], 'completed')
        self.assertEqual(result['next_agent'], 'mavery-qa')
        self.assertEqual(result['next_status'], 'queued')
        arguments = worker.call_args.kwargs
        self.assertEqual(worker.call_args.args[0], 'Safe prompt')
        self.assertFalse(arguments['allow_web'])
        draft = self.fixture.profile_path.parent / 'state' / result['draft_ref']
        self.assertEqual(draft.read_text(), 'Useful draft')
        child = self.fixture.broker.get('alpha', result['next_task_id'])
        self.assertEqual(child['parent_task_id'], result['marketing_task_id'])
        qa_handoff = json.loads((self.fixture.profile_path.parent / 'state'
                                 / result['handoff_ref']).read_text())
        self.assertIn('Youtube draft', qa_handoff['review_title'])
        self.assertEqual(qa_handoff['material_ids'], [])
        self.assertEqual(qa_handoff['draft']['artifact_sha256'], result['draft_sha256'])

    def test_changed_handoff_is_rejected_before_prompt_or_model(self):
        child = self.fixture.broker.get('alpha', self.routed['next_task_id'])
        path = self.fixture.profile_path.parent / 'state' / child['input_ref']
        path.write_text(path.read_text().replace('Teach source checking', 'Changed'))
        with patch('pipeline.marketing_worker._prompt') as prompt, \
                patch('pipeline.marketing_worker.codex_worker.generate') as worker, \
                self.assertRaisesRegex(MarketingError, 'hash changed'):
            run_marketing_task(
                self.fixture.broker, project='alpha', task_id=child['task_id'],
                workspace=self.fixture.workspace, profile_path=self.fixture.profile_path,
                executable='/usr/bin/true')
        prompt.assert_not_called()
        worker.assert_not_called()

    def test_prompt_uses_policy_as_rules_and_payload_as_untrusted_data(self):
        sections = {
            'facts': SimpleNamespace(value={'usage_policy': 'blocked',
                'material_state': 'current', 'content': {'approved_facts': [
                    {'id': 'candidate', 'claim': 'Do not use'}]}}),
            'prohibited_claims': SimpleNamespace(value={'content': {'claims': [
                {'id': 'P1', 'blocked_meaning': 'Universal promise'}]}}),
            'channels': SimpleNamespace(value={'content': {'automatic_fanout': False}}),
        }
        context = SimpleNamespace(sections=sections)
        handoff = {'goal': 'Teach a method', 'channel': 'x', 'audience_id': None,
                   'material_ids': [], 'route': 'content-channel'}
        profile = {'product_motion': 'product-led-software', '_project_context': {}}
        view = {'materials': [], 'audiences': []}
        with patch('pipeline.marketing_worker.resolve_project_context', return_value=context), \
                patch('pipeline.marketing_worker.studio.read', return_value=view):
            prompt = _prompt(self.fixture.workspace, self.fixture.profile_path,
                             profile, handoff)
        self.assertIn('product_claims_allowed": false', prompt)
        self.assertNotIn('Do not use', prompt)
        self.assertIn('Universal promise', prompt)
        self.assertIn('No promotional or engagement call to action', prompt)

    def test_changed_research_evidence_is_rejected_before_prompt_or_model(self):
        child = self.fixture.broker.get('alpha', self.routed['next_task_id'])
        handoff = json.loads((self.fixture.profile_path.parent / 'state'
                              / child['input_ref']).read_text())
        report = self.fixture.profile_path.parent / 'state' / handoff['research']['artifact_ref']
        report.write_text(report.read_text() + '\nChanged')
        with patch('pipeline.marketing_worker._prompt') as prompt, \
                patch('pipeline.marketing_worker.codex_worker.generate') as worker, \
                self.assertRaisesRegex(MarketingError, 'evidence hash changed'):
            run_marketing_task(
                self.fixture.broker, project='alpha', task_id=child['task_id'],
                workspace=self.fixture.workspace, profile_path=self.fixture.profile_path,
                executable='/usr/bin/true')
        prompt.assert_not_called()
        worker.assert_not_called()

    def test_model_failure_marks_marketing_failed_without_qa_child(self):
        with patch('pipeline.marketing_worker._prompt', return_value='Safe prompt'), \
                patch('pipeline.marketing_worker.codex_worker.generate',
                      side_effect=RuntimeError('model unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'model unavailable'):
                run_marketing_task(
                    self.fixture.broker, project='alpha',
                    task_id=self.routed['next_task_id'],
                    workspace=self.fixture.workspace,
                    profile_path=self.fixture.profile_path,
                    executable='/usr/bin/true')
        self.assertEqual(self.fixture.broker.get(
            'alpha', self.routed['next_task_id'])['status'], 'failed')
        self.assertEqual(self.fixture.broker.db.execute(
            'SELECT count(*) FROM tasks WHERE parent_task_id=?',
            (self.routed['next_task_id'],)).fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
