import json
from pathlib import Path
import unittest

from pipeline.sbom import SbomError, generate_sbom, normalize_sbom


class SbomTests(unittest.TestCase):
    def test_normalization_removes_machine_specific_fields_and_sorts(self):
        payload = json.dumps({
            'bomFormat': 'CycloneDX', 'specVersion': '1.5',
            'serialNumber': 'urn:uuid:random',
            'metadata': {'timestamp': 'now', 'tools': [{'version': 'local'}]},
            'components': [{'bom-ref': 'z'}, {'bom-ref': 'a'}],
            'dependencies': [
                {'ref': 'z', 'dependsOn': ['b', 'a']}, {'ref': 'a', 'dependsOn': []}],
        }).encode()
        result = json.loads(normalize_sbom(payload))
        self.assertNotIn('serialNumber', result)
        self.assertNotIn('timestamp', result['metadata'])
        self.assertNotIn('tools', result['metadata'])
        self.assertEqual([item['bom-ref'] for item in result['components']], ['a', 'z'])
        self.assertEqual(result['dependencies'][1]['dependsOn'], ['a', 'b'])

    def test_rejects_unsupported_document(self):
        with self.assertRaises(SbomError):
            normalize_sbom(b'{}')

    def test_checked_in_sbom_matches_current_lock(self):
        workspace = Path(__file__).resolve().parents[2]
        expected = workspace / 'open-source/SBOM.cdx.json'
        if not expected.is_file():
            expected = workspace / 'SBOM.cdx.json'
        self.assertEqual(expected.read_bytes(), generate_sbom(workspace))


if __name__ == '__main__':
    unittest.main()
