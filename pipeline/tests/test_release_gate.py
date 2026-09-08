from pathlib import Path
import subprocess
import tempfile
import unittest

from pipeline.release_gate import ReleaseGateError, scan_history_personal_data


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        subprocess.run(['git', 'init', '-q'], cwd=self.repo, check=True)
        subprocess.run(['git', 'config', 'user.name', 'Release Test'], cwd=self.repo, check=True)
        subprocess.run(['git', 'config', 'user.email', 'release-test@localhost'], cwd=self.repo, check=True)

    def commit(self, path: str, content: str):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        subprocess.run(['git', 'add', path], cwd=self.repo, check=True)
        subprocess.run(['git', 'commit', '-q', '-m', path], cwd=self.repo, check=True)

    def test_clean_history_without_personal_data_passes(self):
        self.commit('README.md', 'Synthetic local example.\n')
        result = scan_history_personal_data(self.repo)
        self.assertEqual(result['commits'], 1)
        self.assertEqual(result['findings'], 0)

    def test_personal_data_in_old_commit_is_rejected(self):
        self.commit('note.txt', 'Contact person' + '@' + 'example.com\n')
        (self.repo / 'note.txt').write_text('Removed later.\n')
        subprocess.run(['git', 'commit', '-qam', 'remove address'], cwd=self.repo, check=True)
        with self.assertRaisesRegex(ReleaseGateError, 'email_address'):
            scan_history_personal_data(self.repo)

    def test_dirty_worktree_is_rejected(self):
        self.commit('README.md', 'Clean.\n')
        (self.repo / 'README.md').write_text('Dirty.\n')
        with self.assertRaisesRegex(ReleaseGateError, 'clean worktree'):
            scan_history_personal_data(self.repo)


if __name__ == '__main__':
    unittest.main()
