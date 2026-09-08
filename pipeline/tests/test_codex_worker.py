import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from pipeline.codex_worker import WorkerError, command, generate


class CodexWorkerTests(unittest.TestCase):
    executable = '/usr/bin/true'

    def fake_start(self, argv, **kwargs):
        self.argv, self.kwargs = argv, kwargs
        output = Path(argv[argv.index('--output-last-message') + 1])
        self.process = Mock(returncode=0, pid=987654)
        self.process.communicate.side_effect = lambda *a, **k: output.write_text('Useful lesson')
        return self.process

    def test_text_result_and_subscription_only_configuration(self):
        with patch('pipeline.codex_worker.subprocess.Popen', side_effect=self.fake_start), patch.dict(
                os.environ, {'OPENAI_API_KEY': 'test-secret', 'ANTHROPIC_API_KEY': 'other-secret'}):
            self.assertEqual(generate('A brief', executable=self.executable), 'Useful lesson')
        self.assertNotIn('OPENAI_API_KEY', self.kwargs['env'])
        self.assertNotIn('ANTHROPIC_API_KEY', self.kwargs['env'])
        self.assertIn('forced_login_method="chatgpt"', self.argv)
        self.assertIn('--ignore-user-config', self.argv)
        self.assertIn('web_search="disabled"', self.argv)
        self.assertIn('read-only', self.argv)
        self.assertTrue(self.kwargs['start_new_session'])

    def test_read_only_research_can_enable_built_in_web_without_shell_or_keys(self):
        with patch('pipeline.codex_worker.subprocess.Popen', side_effect=self.fake_start):
            self.assertEqual(generate('Research', executable=self.executable,
                                      allow_web=True), 'Useful lesson')
        self.assertIn('web_search="live"', self.argv)
        self.assertIn('--disable', self.argv)
        self.assertIn('shell_tool', self.argv)

    def test_invalid_brief_and_timeout_never_spawn(self):
        with patch('pipeline.codex_worker.subprocess.Popen') as start:
            for brief, timeout in (('', 20), ('x' * 64001, 20), ('brief', True), ('brief', 301)):
                with self.assertRaises(WorkerError):
                    generate(brief, executable=self.executable, timeout=timeout)
            start.assert_not_called()

    def test_nonzero_exit_does_not_retry(self):
        with patch('pipeline.codex_worker.subprocess.Popen', return_value=Mock(returncode=1)) as start:
            with self.assertRaises(WorkerError):
                generate('A brief', executable=self.executable)
            self.assertEqual(start.call_count, 1)

    def test_timeout_kills_group_and_reaps_process(self):
        process = Mock(pid=987654)
        process.communicate.side_effect = subprocess.TimeoutExpired('codex', 1)
        with patch('pipeline.codex_worker.subprocess.Popen', return_value=process), patch(
                'pipeline.codex_worker.os.killpg') as kill:
            with self.assertRaisesRegex(WorkerError, 'timed out'):
                generate('A brief', executable=self.executable, timeout=1)
            kill.assert_called_once()
            process.wait.assert_called_once()

    def test_missing_output_is_failure(self):
        with patch('pipeline.codex_worker.subprocess.Popen', return_value=Mock(returncode=0)):
            with self.assertRaisesRegex(WorkerError, 'missing'):
                generate('A brief', executable=self.executable)
