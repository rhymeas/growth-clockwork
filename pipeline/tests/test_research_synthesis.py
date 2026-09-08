from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from pipeline.research_synthesis import synthesize, read_saved, _load_key


class SynthesisTests(unittest.TestCase):
    @patch.dict('os.environ', {'OPENAI_API_KEY':'sk-fixture-environment-not-real'})
    @patch('pipeline.research_synthesis.subprocess.run')
    def test_server_environment_needs_no_macos_helper(self, run):
        self.assertEqual(_load_key(None),'sk-fixture-environment-not-real')
        run.assert_not_called()

    @patch.dict('os.environ', {'OPENAI_API_KEY':'invalid-secret-value'})
    def test_invalid_environment_error_does_not_expose_value(self):
        with self.assertRaisesRegex(ValueError,'^missing_or_invalid_openai_api_key$'):
            _load_key(None)

    @patch.dict('os.environ', {'OPENAI_API_KEY':'sk-fixture-environment-not-real'})
    def test_explicit_file_selection_does_not_silently_use_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'key.txt'
            path.write_text('sk-fixture-file-credential-not-real')
            self.assertEqual(_load_key(path),'sk-fixture-file-credential-not-real')
            path.write_text('not a key')
            with self.assertRaisesRegex(ValueError,'ambiguous_key_file'):
                _load_key(path)

    @patch('pipeline.research_synthesis.subprocess.run')
    @patch('pipeline.research_synthesis.root_writer._read_relative_bytes', return_value=b'{}')
    @patch('pipeline.research_synthesis.root_writer._project_state_fd', return_value=nullcontext(1))
    @patch('pipeline.research_synthesis.GoalLoopService')
    @patch('pipeline.research_synthesis.collect', return_value={'record': 'record.json'})
    def test_saved_attempt_is_not_dispatched_twice(self, collect, factory, fd, read, run):
        with tempfile.TemporaryDirectory() as temporary:
            workspace=Path(temporary)
            project=workspace/'projects/example'
            project.mkdir(parents=True)
            (project/'model-policy.json').write_text(json.dumps({'provider':'openai','model':'fixture',
                'budget_mode':'operator_uncapped','automatic_retries':0,
                'method_pack':'automation/marketing-methods/dist/pack.json',
                'dispatcher':'automation/marketing-methods/dispatch-discovery.mjs',
                'editorial_policy_revision':'test-policy'}))
            pack=workspace/'automation/marketing-methods/dist'
            pack.mkdir(parents=True)
            (pack/'pack.json').write_text('{"version":"fixture"}')
            (pack.parent/'dispatch-discovery.mjs').write_text('// Test-only placeholder; subprocess is mocked')
            key=workspace/'fixture-key.txt'
            key.write_text('sk-test-only-not-a-real-secret')
            service=factory.return_value
            service.workspace=workspace
            service._selected.return_value=(project/'project.json',{'_state_root':'state'})
            service._current_brief.return_value=({'artifact_ref':'brief.json'}, {})
            records={}
            service._read_state_json.side_effect=lambda profile, path: records.get(path)
            service._write_record.side_effect=lambda pp,p,j,k,path,value: records.__setitem__(path,value)
            run.side_effect=[subprocess.CompletedProcess([],0,'{}',''),
                             subprocess.CompletedProcess([],0,'{"status":"completed","usage":{"input_tokens":10}}','')]
            first=synthesize(workspace,key,'example-project')
            second=synthesize(workspace,key,'example-project')
            self.assertEqual(first['status'],'completed')
            self.assertTrue(second['reused'])
            self.assertEqual(run.call_count,2)  # one preflight, one provider dispatch
            self.assertNotIn('sk-test',json.dumps(list(records.values())))
            calls_before_read=collect.call_count
            saved=read_saved(workspace,'example-project')
            self.assertEqual(saved['status'],'completed')
            self.assertEqual(run.call_count,2)
            self.assertEqual(collect.call_count,calls_before_read)
            records.pop(next(path for path in records if path.name=='result-r1.json'))
            pending=synthesize(workspace,key,'example-project')
            self.assertEqual(pending['status'],'attempt_already_claimed_no_retry')
            self.assertEqual(run.call_count,2)


if __name__=='__main__':
    unittest.main()
