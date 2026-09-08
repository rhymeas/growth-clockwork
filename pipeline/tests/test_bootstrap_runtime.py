import json
import os
from pathlib import Path
import tempfile
import unittest

from pipeline.bootstrap_runtime import BootstrapError, bootstrap


class BootstrapRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        (self.workspace / 'AGENTS.md').write_text('# Local test workspace\n')
        runtime = self.workspace / 'runtime'
        runtime.mkdir()
        package_root = Path(__file__).resolve().parents[2]
        source = package_root / 'open-source/runtime/permissions.example.json'
        if not source.is_file():
            source = package_root / 'runtime/permissions.example.json'
        (runtime / 'permissions.example.json').write_bytes(source.read_bytes())

    def test_creates_private_review_runtime_once(self):
        first = bootstrap(self.workspace)
        second = bootstrap(self.workspace)
        self.assertTrue(first['permissions_created'])
        self.assertFalse(second['permissions_created'])
        self.assertEqual(first['publish'], 'review')
        permissions = self.workspace / first['permissions']
        database = self.workspace / first['database']
        self.assertTrue(database.is_file())
        self.assertEqual(os.stat(permissions).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(database).st_mode & 0o777, 0o600)
        self.assertFalse(json.loads(permissions.read_text())['agents']['research']['autostart'])

    def test_existing_permissions_are_not_overwritten(self):
        target = self.workspace / 'runtime/permissions.json'
        target.write_text('{"invalid":true}')
        with self.assertRaises(Exception):
            bootstrap(self.workspace)
        self.assertEqual(target.read_text(), '{"invalid":true}')

    def test_symlinked_private_permission_file_is_rejected(self):
        target = self.workspace / 'runtime/permissions.json'
        target.symlink_to(self.workspace / 'runtime/permissions.example.json')
        with self.assertRaisesRegex(BootstrapError, 'real file'):
            bootstrap(self.workspace)


if __name__ == '__main__':
    unittest.main()
