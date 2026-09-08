from __future__ import annotations

import json
import threading
import unittest
from unittest.mock import patch

from pipeline.broker_automation import BrokerAutomation
from pipeline.inbox_worker import route_task
from pipeline.marketing_worker import run_marketing_task
from pipeline.mavery_qa_worker import run_mavery_qa_task
from pipeline.research_worker import run_research_task
from pipeline.tests.test_inbox_worker import InboxWorkerTests


class BrokerAutomationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = InboxWorkerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        parent, _ = self.fixture.proposal_task()
        routed = route_task(
            self.fixture.broker, project='alpha', task_id=parent['task_id'],
            workspace=self.fixture.workspace,
            profile_path=self.fixture.profile_path)
        self.task_id = routed['research_task_id']

    def test_single_lane_deduplicates_active_start(self):
        started = threading.Event()
        release = threading.Event()
        calls = []

        def runner(*args, **kwargs):
            calls.append(kwargs['task_id'])
            started.set()
            release.wait(2)

        service = BrokerAutomation(
            workspace=self.fixture.workspace,
            database=self.fixture.workspace / 'tasks.sqlite',
            permissions=self.fixture.permissions, executable='/usr/bin/true',
            runner=runner)
        try:
            self.assertTrue(service.start(
                project='alpha', task_id=self.task_id,
                profile_path=self.fixture.profile_path))
            self.assertTrue(started.wait(2))
            self.assertFalse(service.start(
                project='alpha', task_id=self.task_id,
                profile_path=self.fixture.profile_path))
            self.assertEqual(calls, [self.task_id])
        finally:
            release.set()
            service.close()

    def test_wait_until_idle_observes_the_running_project_chain(self):
        release = threading.Event()

        def runner(*args, **kwargs):
            release.wait(2)

        service = BrokerAutomation(
            workspace=self.fixture.workspace,
            database=self.fixture.workspace / 'tasks.sqlite',
            permissions=self.fixture.permissions, executable='/usr/bin/true',
            runner=runner)
        try:
            self.assertTrue(service.start(
                project='alpha', task_id=self.task_id,
                profile_path=self.fixture.profile_path))
            timer = threading.Timer(0.05, release.set)
            timer.start()
            self.assertTrue(service.wait_until_idle('alpha', timeout=2))
            timer.join()
            with self.assertRaisesRegex(ValueError, 'Invalid automation wait'):
                service.wait_until_idle('alpha', timeout=0)
        finally:
            release.set()
            service.close()

    def test_preflight_failure_becomes_visible_failed_task(self):
        finished = threading.Event()

        def runner(*args, **kwargs):
            try:
                raise RuntimeError('private failure detail')
            finally:
                finished.set()

        service = BrokerAutomation(
            workspace=self.fixture.workspace,
            database=self.fixture.workspace / 'tasks.sqlite',
            permissions=self.fixture.permissions, executable='/usr/bin/true',
            runner=runner)
        try:
            self.assertTrue(service.start(
                project='alpha', task_id=self.task_id,
                profile_path=self.fixture.profile_path))
            self.assertTrue(finished.wait(2))
            for _ in range(100):
                if self.fixture.broker.get('alpha', self.task_id)['status'] == 'failed':
                    break
                threading.Event().wait(0.005)
            self.assertEqual(
                self.fixture.broker.get('alpha', self.task_id)['status'], 'failed')
            audit = self.fixture.broker.db.execute(
                "SELECT details_json FROM audit_events WHERE task_id=? AND event_type='failed'",
                (self.task_id,)).fetchone()
            self.assertEqual(audit['details_json'], '{}')
        finally:
            service.close()

    def test_publisher_can_run_without_codex_executable(self):
        self.fixture.config['agents']['publisher'] = {
            'enabled': True, 'browser': 'none',
            'credentials': 'per-channel-connector-only',
        }
        self.fixture.permissions.write_text(json.dumps(self.fixture.config))
        task = self.fixture.broker.admit(
            project='alpha', origin='connector', origin_key='publisher-test',
            agent='publisher', input_ref='records/publisher-requests/test.json',
            input_sha256='b' * 64, actor='local-operator', not_before=0)
        finished = threading.Event()

        def runner(*args, **kwargs):
            self.assertIsNone(kwargs['executable'])
            finished.set()

        service = BrokerAutomation(
            workspace=self.fixture.workspace,
            database=self.fixture.workspace / 'tasks.sqlite',
            permissions=self.fixture.permissions, executable=None,
            runners={'publisher': runner}, enabled_agents={'publisher'})
        try:
            self.assertTrue(service.start(
                project='alpha', task_id=task['task_id'],
                profile_path=self.fixture.profile_path))
            self.assertTrue(finished.wait(2))
        finally:
            service.close()

    def test_research_completion_automatically_starts_marketing(self):
        marketing_started = threading.Event()
        calls = []

        def research_runner(*args, **kwargs):
            calls.append('research')
            with patch('pipeline.research_worker.codex_worker.generate',
                       return_value='Source: https://example.org'):
                return run_research_task(*args, **kwargs)

        def marketing_runner(*args, **kwargs):
            calls.append('marketing')
            marketing_started.set()

        service = BrokerAutomation(
            workspace=self.fixture.workspace,
            database=self.fixture.workspace / 'tasks.sqlite',
            permissions=self.fixture.permissions, executable='/usr/bin/true',
            runners={'research': research_runner, 'marketing': marketing_runner})
        try:
            self.assertTrue(service.start(
                project='alpha', task_id=self.task_id,
                profile_path=self.fixture.profile_path))
            self.assertTrue(marketing_started.wait(2))
            self.assertEqual(calls, ['research', 'marketing'])
            research = self.fixture.broker.get('alpha', self.task_id)
            self.assertEqual(research['status'], 'completed')
            marketing = self.fixture.broker.db.execute(
                "SELECT agent_id, status FROM tasks WHERE parent_task_id=?",
                (self.task_id,)).fetchone()
            self.assertEqual((marketing['agent_id'], marketing['status']),
                             ('marketing', 'queued'))
        finally:
            service.close()

    def test_single_lane_runs_research_marketing_and_qa_in_order(self):
        finished = threading.Event()

        def research_runner(*args, **kwargs):
            with patch('pipeline.research_worker.codex_worker.generate',
                       return_value='Candidate source: https://example.org'):
                return run_research_task(*args, **kwargs)

        def marketing_runner(*args, **kwargs):
            with patch('pipeline.marketing_worker._prompt', return_value='Draft safely'), \
                    patch('pipeline.marketing_worker.codex_worker.generate',
                          return_value='A complete useful lesson.'):
                return run_marketing_task(*args, **kwargs)

        def qa_runner(*args, **kwargs):
            try:
                with patch('pipeline.mavery_qa_worker._prompt', return_value='Review safely'), \
                        patch('pipeline.mavery_qa_worker.codex_worker.generate',
                              return_value='No issue found in this limited pass.'):
                    return run_mavery_qa_task(*args, **kwargs)
            finally:
                finished.set()

        service = BrokerAutomation(
            workspace=self.fixture.workspace,
            database=self.fixture.workspace / 'tasks.sqlite',
            permissions=self.fixture.permissions, executable='/usr/bin/true',
            runners={'research': research_runner, 'marketing': marketing_runner,
                     'mavery-qa': qa_runner})
        try:
            self.assertTrue(service.start(
                project='alpha', task_id=self.task_id,
                profile_path=self.fixture.profile_path))
            self.assertTrue(finished.wait(3))
            for _ in range(100):
                rows = self.fixture.broker.db.execute(
                    "SELECT task_id, agent_id, status FROM tasks ORDER BY created_at, rowid").fetchall()
                if rows[-1]['status'] == 'awaiting_review':
                    break
                threading.Event().wait(0.005)
            self.assertEqual([(row['agent_id'], row['status']) for row in rows], [
                ('inbox', 'completed'), ('research', 'completed'),
                ('marketing', 'completed'), ('mavery-qa', 'awaiting_review')])
            self.assertEqual(len([item for item in self.fixture.fixture.service.reviews('alpha')['reviews']
                                  if item['artifact_id'] == rows[-1]['task_id']]), 1)
        finally:
            service.close()


if __name__ == '__main__':
    unittest.main()
