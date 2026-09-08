import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from pipeline.codex_worker import run_task
from pipeline.codex_worker import WorkerError
from pipeline.broker_writer import reconcile_review
from pipeline.review_api import ReviewError
from pipeline.task_broker import BrokerError, TaskBroker
from pipeline.tests import test_review_api


class BrokerReviewFlowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_review_api.ReviewAPITests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        root = Path(self.fixture.temp.name)
        permissions = root / 'permissions.json'
        permissions.write_text(json.dumps({'publish': 'review', 'agents': {
            'marketing': {'browser': 'none', 'credentials': 'none'}}}))
        self.broker = TaskBroker(root / 'tasks.sqlite', permissions, clock=lambda: 100)
        self.addCleanup(self.broker.close)
        brief = 'Write a useful lesson'
        task = self.broker.admit(project='alpha', origin='operator', origin_key='brief',
                                 agent='marketing', input_ref='brief.md',
                                 input_sha256=hashlib.sha256(brief.encode()).hexdigest(),
                                 actor='operator', not_before=100)
        profile, _ = self.fixture.service._selected_profile('alpha')
        self.args = dict(project='alpha', task=task['task_id'], agent='marketing', brief=brief,
                         executable='/usr/bin/true', workspace=self.fixture.workspace,
                         profile_path=profile, output_ref=f"outbox/artifacts/{task['task_id']}/r1.md",
                         review_title='A useful draft')

    def test_worker_result_is_readable_in_existing_review_queue(self):
        with patch('pipeline.codex_worker.generate', return_value='A useful lesson'):
            result = run_task(self.broker, **self.args)
        self.assertEqual(result['status'], 'awaiting_review')
        items = self.fixture.service.reviews('alpha')['reviews']
        item = next(item for item in items if item['artifact_id'] == self.args['task'])
        self.assertEqual(item['artifact_content'], 'A useful lesson')
        self.assertEqual(item['status'], 'pending')
        self.assertTrue(all(check['status'] == 'not_run' for check in item['quality_checks']))
        self.assertEqual(item['evidence'], [])
        self.assertFalse(any(item['artifact_id'] == self.args['task']
                             for item in self.fixture.service.reviews('beta')['reviews']))

    def test_trusted_dispatcher_may_derive_prompt_but_not_change_admitted_input(self):
        with patch('pipeline.codex_worker.generate', return_value='Draft') as generate:
            result = run_task(
                self.broker, **(self.args | {'brief': 'Trusted derived prompt',
                                            'admitted_input': 'Write a useful lesson'}))
        self.assertEqual(result['status'], 'awaiting_review')
        generate.assert_called_once_with(
            'Trusted derived prompt', executable='/usr/bin/true', timeout=180)

    def test_changed_admitted_input_rejected_before_model_or_claim(self):
        with patch('pipeline.codex_worker.generate') as generate, self.assertRaises(BrokerError):
            run_task(self.broker, **(self.args | {
                'brief': 'Trusted derived prompt', 'admitted_input': 'Changed'}))
        generate.assert_not_called()
        self.assertEqual(self.broker.get('alpha', self.args['task'])['status'], 'queued')

    def test_invalid_review_path_rejected_before_model_call(self):
        with patch('pipeline.codex_worker.generate') as generate, self.assertRaises(ReviewError):
            run_task(self.broker, **(self.args | {'output_ref': 'records/not-review.md'}))
        generate.assert_not_called()
        self.assertEqual(self.broker.get('alpha', self.args['task'])['status'], 'queued')

    def test_review_rechecks_generated_bytes(self):
        with patch('pipeline.codex_worker.generate', return_value='A useful lesson'):
            run_task(self.broker, **self.args)
        target = self.fixture.workspace / 'projects/alpha-profile/state' / self.args['output_ref']
        target.write_text('Changed after acceptance')
        with self.assertRaisesRegex(ReviewError, 'Artifact bytes'):
            self.fixture.service.reviews('alpha')

    def decide(self, action, **extra):
        with patch('pipeline.codex_worker.generate', return_value='A useful lesson'):
            run_task(self.broker, **self.args)
        item = next(item for item in self.fixture.service.reviews('alpha')['reviews']
                    if item['artifact_id'] == self.args['task'])
        request = {key: item[key] for key in ('project_id', 'project_profile_revision',
                                             'artifact_id', 'artifact_revision', 'artifact_sha256')}
        return self.fixture.service.review_action(request | {'action': action} | extra)[0]

    def reconcile(self):
        return reconcile_review(self.broker, self.fixture.service, project='alpha', task=self.args['task'])

    def test_verified_approval_completes_task_idempotently(self):
        self.decide('approve')
        result = self.reconcile()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.reconcile(), result)
        self.assertEqual(self.broker.db.execute('SELECT count(*) FROM decisions').fetchone()[0], 1)

    def test_verified_decline_closes_task_without_approval(self):
        self.decide('decline', reason='Not useful enough')
        self.assertEqual(self.reconcile()['status'], 'cancelled')
        self.assertEqual(self.broker.db.execute('SELECT action FROM decisions').fetchone()[0], 'decline')

    def test_verified_note_preserves_reference_without_automatic_rework(self):
        self.decide('note', note='Please check the source')
        self.assertEqual(self.reconcile()['status'], 'cancelled')
        decision = self.broker.db.execute('SELECT action, note_ref FROM decisions').fetchone()
        self.assertEqual(decision['action'], 'note')
        self.assertIsNotNone(decision['note_ref'])
        self.assertEqual(self.broker.db.execute('SELECT count(*) FROM tasks').fetchone()[0], 1)

    def test_missing_decision_leaves_task_pending(self):
        with patch('pipeline.codex_worker.generate', return_value='A useful lesson'):
            run_task(self.broker, **self.args)
        self.assertEqual(self.reconcile()['status'], 'awaiting_review')
        self.assertEqual(self.broker.db.execute('SELECT count(*) FROM decisions').fetchone()[0], 0)

    def test_unreceipted_decision_cannot_close_task(self):
        record = self.decide('approve')
        _, profile = self.fixture.service._selected_profile('alpha')
        from pipeline import root_writer
        request = self.fixture.service._writer_request(profile, record)
        path = self.fixture.service._state_root(profile) / root_writer._receipt_relative(profile, request)
        path.unlink()
        with self.assertRaisesRegex(ReviewError, 'receipt'):
            self.reconcile()
        self.assertEqual(self.broker.get('alpha', self.args['task'])['status'], 'awaiting_review')

    def test_audit_failure_rolls_back_mirror_but_preserves_original_review(self):
        self.decide('approve')
        with patch.object(self.broker, 'event', side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):
                self.reconcile()
        self.assertEqual(self.broker.db.execute('SELECT count(*) FROM decisions').fetchone()[0], 0)
        self.assertEqual(self.broker.get('alpha', self.args['task'])['status'], 'awaiting_review')
        self.assertEqual(self.reconcile()['status'], 'completed')

    def allow_qa(self):
        profile_path = self.args['profile_path']
        profile = json.loads(profile_path.read_text())
        profile['writer']['allowed_roots'].append('evidence/packets')
        profile['writer']['append_only_roots'].append('evidence/packets')
        profile_path.write_text(json.dumps(profile))

    def test_separate_qa_pass_is_pinned_evidence_not_approval(self):
        self.allow_qa()
        with patch('pipeline.codex_worker.generate', side_effect=['Draft text', 'Unsupported claim: revise sentence 1.']) as model:
            result = run_task(self.broker, **self.args, qa=True)
        self.assertEqual(model.call_count, 2)
        self.assertIn('Draft text', model.call_args_list[1].args[0])
        self.assertEqual(result['status'], 'awaiting_review')
        item = next(item for item in self.fixture.service.reviews('alpha')['reviews']
                    if item['artifact_id'] == self.args['task'])
        self.assertEqual(item['quality_checks'][1]['status'], 'warn')
        self.assertEqual(item['quality_checks'][0]['status'], 'not_run')
        evidence = item['evidence'][0]
        report = self.fixture.service.evidence('alpha', evidence['ref'], evidence['sha256'])
        self.assertIn('Unsupported claim', str(report))
        self.assertIn(hashlib.sha256(b'Draft text').hexdigest(), str(report))
        self.assertEqual(self.broker.db.execute('SELECT count(*) FROM decisions').fetchone()[0], 0)

    def test_qa_failure_leaves_no_partial_review_package(self):
        self.allow_qa()
        with patch('pipeline.codex_worker.generate', side_effect=['Draft text', WorkerError('quota')]):
            with self.assertRaises(WorkerError):
                run_task(self.broker, **self.args, qa=True)
        self.assertEqual(self.broker.get('alpha', self.args['task'])['status'], 'failed')
        self.assertFalse(any(item['artifact_id'] == self.args['task']
                             for item in self.fixture.service.reviews('alpha')['reviews']))

    def test_qa_permissions_preflight_precedes_model_calls(self):
        with patch('pipeline.codex_worker.generate') as model, self.assertRaises(WorkerError):
            run_task(self.broker, **self.args, qa=True)
        model.assert_not_called()
        self.assertEqual(self.broker.get('alpha', self.args['task'])['status'], 'queued')
