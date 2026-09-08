import hashlib
import json
from pathlib import Path, PurePosixPath
import tempfile
import unittest

from pipeline.package_audit import PackageAuditError, audit_candidate, scan_text
from pipeline.package_export import export_candidate


class PackageAuditTests(unittest.TestCase):
    def test_current_export_passes_bounded_technical_audit(self):
        workspace = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / 'candidate'
            export_candidate(workspace, candidate)
            result = audit_candidate(candidate)
        self.assertEqual(result['status'], 'technical_audit_passed')
        self.assertGreater(result['candidate_files'], 250)
        self.assertTrue(result['license_granted'])
        self.assertEqual(result['license'], 'Apache-2.0')
        self.assertFalse(result['published'])
        self.assertIn('MIT', result['dependency_license_counts'])

    def test_tampered_export_is_rejected(self):
        workspace = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / 'candidate'
            export_candidate(workspace, candidate)
            (candidate / 'AGENTS.md').write_text('changed')
            with self.assertRaisesRegex(PackageAuditError, 'differs from receipt'):
                audit_candidate(candidate)

    def test_high_confidence_secrets_and_user_paths_are_flagged(self):
        findings = scan_text(
            PurePosixPath('example.txt'),
            '/' + 'Users/alice/project\nAuthorization: Bearer ' + 'x' * 40)
        self.assertEqual(findings, [
            'example.txt: absolute_user_path', 'example.txt: bearer_header'])

    def test_explicit_short_dummy_key_is_not_misreported_as_real(self):
        self.assertEqual(scan_text(
            PurePosixPath('test.py'), 'OPENAI_API_KEY=sk-fixture-not-real'), [])

    def test_git_history_directory_is_not_treated_as_package_content(self):
        workspace = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / 'candidate'
            export_candidate(workspace, candidate)
            (candidate / '.git/objects').mkdir(parents=True)
            (candidate / '.git/objects/internal').write_text('git metadata')
            result = audit_candidate(candidate)
        self.assertEqual(result['status'], 'technical_audit_passed')

    def test_symlinked_git_history_is_rejected(self):
        workspace = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / 'candidate'
            export_candidate(workspace, candidate)
            (candidate / '.git').symlink_to(Path(directory))
            with self.assertRaisesRegex(PackageAuditError, 'real directory'):
                audit_candidate(candidate)

    def test_unreviewed_bundled_media_is_rejected(self):
        workspace = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / 'candidate'
            receipt = export_candidate(workspace, candidate)
            media = candidate / 'image.png'
            media.write_bytes(b'not-real-media')
            receipt['files'].append({
                'path': 'image.png', 'bytes': len(media.read_bytes()),
                'sha256': hashlib.sha256(media.read_bytes()).hexdigest()})
            (candidate / 'EXPORT-RECEIPT.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(PackageAuditError, 'provenance review'):
                audit_candidate(candidate)


if __name__ == '__main__':
    unittest.main()
