import json
from pathlib import Path
import tempfile
import unittest

from pipeline.package_export import candidate_files, export_candidate


class PackageExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'source'
        self.root.mkdir()
        (self.root / 'core').mkdir()
        (self.root / 'core/app.py').write_text('print("example")')
        self.manifest = dict(boundary_revision='test', include_entries=[{'path': 'core'}],
                             package_root_files=[],
                             exclude_entries=[{'path': 'core/state'}], export_rules=dict(
                                 forbidden_directory_names=['node_modules', '.git'],
                                 forbidden_file_names=['auth.json'], forbidden_file_suffixes=['.sqlite']))
        (self.root / 'open-source-package.json').write_text(json.dumps(self.manifest))

    def test_export_hashes_files_and_does_not_overwrite(self):
        destination = Path(self.temp.name) / 'export'
        receipt = export_candidate(self.root, destination)
        self.assertFalse(receipt['published'])
        self.assertTrue(receipt['license_granted'])
        self.assertEqual(receipt['license'], 'Apache-2.0')
        self.assertEqual(len(receipt['files']), 2)
        self.assertTrue((destination / 'AGENTS.md').is_file())
        self.assertEqual((destination / 'core/app.py').read_bytes(), (self.root / 'core/app.py').read_bytes())
        with self.assertRaises(FileExistsError):
            export_candidate(self.root, destination)

    def test_private_generated_and_environment_files_are_excluded(self):
        for name in ('core/.env.production', 'core/auth.json', 'core/tasks.sqlite',
                     'core/state/private.txt', 'core/node_modules/code.js', 'core/.git/config'):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('DO NOT EXPORT')
        self.assertEqual({str(path) for path in candidate_files(self.root, self.manifest)}, {'core/app.py'})

    def test_symlinks_and_traversal_are_rejected(self):
        (self.root / 'core/link').symlink_to('/tmp')
        with self.assertRaises(ValueError):
            candidate_files(self.root, self.manifest)
        self.manifest['include_entries'] = [{'path': '../outside'}]
        with self.assertRaises(ValueError):
            candidate_files(self.root, self.manifest)

    def test_export_inside_source_is_rejected(self):
        with self.assertRaises(ValueError):
            export_candidate(self.root, self.root / 'nested')

    def test_package_root_file_is_renamed_and_receipted(self):
        (self.root / 'metadata').mkdir()
        (self.root / 'metadata/README.md').write_text('standalone')
        self.manifest['package_root_files'] = [
            {'source': 'metadata/README.md', 'path': 'README.md'}]
        (self.root / 'open-source-package.json').write_text(json.dumps(self.manifest))
        destination = Path(self.temp.name) / 'export'
        receipt = export_candidate(self.root, destination)
        self.assertEqual((destination / 'README.md').read_text(), 'standalone')
        item = next(item for item in receipt['files'] if item['path'] == 'README.md')
        self.assertEqual(item['origin'], 'metadata/README.md')

    def test_exported_manifest_uses_standalone_sources(self):
        (self.root / 'metadata').mkdir()
        (self.root / 'metadata/README.md').write_text('standalone')
        self.manifest['include_entries'].append({'path': 'open-source-package.json'})
        self.manifest['package_root_files'] = [
            {'source': 'metadata/README.md', 'path': 'README.md'}]
        self.manifest['status'] = {
            'current_repository_is_open_source': False,
            'package_is_published': True,
        }
        self.manifest['future_license'] = {
            'applies_to_current_repository': False,
        }
        (self.root / 'open-source-package.json').write_text(json.dumps(self.manifest))
        destination = Path(self.temp.name) / 'export'
        export_candidate(self.root, destination)
        exported = json.loads((destination / 'open-source-package.json').read_text())
        self.assertEqual(exported['package_root_files'], [
            {'source': 'README.md', 'path': 'README.md'}])
        self.assertTrue(exported['status']['current_repository_is_open_source'])
        self.assertTrue(exported['status']['package_is_published'])
        self.assertTrue(exported['future_license']['applies_to_current_repository'])
