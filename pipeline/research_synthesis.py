"""One durable, explicitly authorized OpenAI discovery run; no publishing."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import uuid
from datetime import datetime, timezone

from pipeline import root_writer
from pipeline.feed_intake import collect, _load_feeds, _receipt_identity
from pipeline.goal_loop import GoalLoopService


def _model_policy(policy):
    # Local adapter routing and a provenance label do not alter model inputs.
    # Exact prompt bytes are separately bound by pack_version.
    return {key: value for key, value in policy.items()
            if key not in {'method_pack', 'dispatcher', 'editorial_policy_revision'}}


def _run_identity(receipt, policy, pack_version):
    job_id = 'DISCOVERY-' + hashlib.sha256(receipt).hexdigest()[:24]
    identity = json.dumps([job_id, _model_policy(policy), pack_version], sort_keys=True).encode()
    run_id = 'SYNTH-' + hashlib.sha256(identity).hexdigest()[:24]
    return job_id, run_id, PurePosixPath('records/research-synthesis') / run_id


def _adapter_path(workspace, policy, field):
    """Optional executable adapter paths come from trusted project config only."""
    value = policy.get(field)
    if not isinstance(value, str):
        raise ValueError('missing_synthesis_adapter')
    relative = PurePosixPath(value)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('invalid_synthesis_adapter')
    path = workspace
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('invalid_synthesis_adapter')
    if not path.is_file():
        raise ValueError('missing_synthesis_adapter')
    return path


def read_saved(workspace: Path, project_id: str):
    """Read the current brief/week/policy result without credentials or dispatch."""
    service = GoalLoopService(workspace)
    try:
        profile_path, profile = service._selected(project_id)
        policy_path = profile_path.parent / 'model-policy.json'
        if not policy_path.is_file() or policy_path.is_symlink():
            return None
        pointer, _ = service._current_brief(profile_path, profile)
        feeds = _load_feeds(profile_path)
        week = datetime.now(timezone.utc).strftime('%G-W%V')
        _, relative = _receipt_identity(project_id, pointer, week, feeds)
        with root_writer._project_state_fd(service.workspace, profile['_state_root']) as fd:
            receipt = root_writer._read_relative_bytes(fd, relative, missing_ok=True)
        if receipt is None:
            return None
        policy = json.loads(policy_path.read_text())
        pack = json.loads(_adapter_path(workspace, policy, 'method_pack').read_text())
        _, _, base = _run_identity(receipt, policy, pack['version'])
        request = service._read_state_json(profile, base / 'request-r1.json')
        result = service._read_state_json(profile, base / 'result-r1.json')
        if request is None:
            return None
        if request.get('brief') != pointer or _model_policy(request.get('policy', {})) != _model_policy(policy) or request.get('pack_version') != pack['version']:
            raise ValueError('synthesis_identity_mismatch')
        if result is None:
            return {'status': 'claimed', 'analysis': None}
        analysis = result.get('result') if result.get('status') == 'completed' else None
        sources = []
        if analysis:
            for source in json.loads(receipt)['sources']:
                for item in source['items']:
                    source_id = 'FEED-' + hashlib.sha256(item['url'].encode()).hexdigest()[:16]
                    if source_id in analysis['evidence_ids']:
                        sources.append({'id': source_id, 'title': item['title'], 'url': item['url']})
        return {'status': result['status'], 'analysis': analysis, 'sources': sources,
                'model': result.get('actual_model'), 'usage': result.get('usage')}
    finally:
        service.close()


def _load_key(key_file: Path | None) -> str:
    if key_file is None:
        key = os.environ.get('OPENAI_API_KEY', '').strip()
        if not re.fullmatch(r'sk-[A-Za-z0-9_-]{16,}', key):
            raise ValueError('missing_or_invalid_openai_api_key')
        return key
    if key_file.is_symlink() or not key_file.is_file() or key_file.stat().st_size > 32768:
        raise ValueError('invalid_key_file')
    if key_file.suffix.lower() == '.rtf':
        converted = subprocess.run(['/usr/bin/textutil', '-convert', 'txt', '-stdout', str(key_file)], capture_output=True, timeout=10)
        if converted.returncode:
            raise ValueError('key_file_unreadable')
        text = converted.stdout.decode()
    else:
        text = key_file.read_text()
    text = re.sub('[\u200B-\u200D\uFEFF\u00AD]', '', text)
    keys = set(re.findall(r'sk-[A-Za-z0-9_-]{16,}', text))
    if len(keys) != 1:
        raise ValueError('ambiguous_key_file')
    return next(iter(keys))


def synthesize(workspace: Path, key_file: Path | None, project_id: str) -> dict:
    intake = collect(workspace, project_id)
    service = GoalLoopService(workspace)
    try:
        profile_path, profile = service._selected(project_id)
        pointer, _ = service._current_brief(profile_path, profile)
        def read(relative):
            with root_writer._project_state_fd(service.workspace, profile['_state_root']) as fd:
                return root_writer._read_relative_bytes(fd, PurePosixPath(relative))
        receipt = read(intake['record'])
        brief = read(pointer['artifact_ref'])
        policy = json.loads((profile_path.parent / 'model-policy.json').read_text())
        if policy['budget_mode'] != 'operator_uncapped' or policy['provider'] != 'openai' or policy['automatic_retries'] != 0:
            raise ValueError('unsupported_model_policy')
        pack = json.loads(_adapter_path(workspace, policy, 'method_pack').read_text())
        job_id, run_id, base = _run_identity(receipt, policy, pack['version'])
        existing = service._read_state_json(profile, base / 'result-r1.json')
        if existing is not None:
            return {'reused': True, 'run_id': run_id, 'status': existing['status']}
        if service._read_state_json(profile, base / 'request-r1.json') is not None:
            return {'reused': True, 'run_id': run_id, 'status': 'attempt_already_claimed_no_retry'}
        api_key = _load_key(key_file)
        dispatch = {'id': run_id, 'status': 'claimed', 'budget_mode': 'operator_uncapped',
                    'job_id': job_id, 'job_revision': 'r1', 'attempt': 1,
                    'provider': policy['provider'], 'model': policy['model']}
        config = {**policy, 'stage': 'audience', 'paused': False, 'active_channels': [],
                  'policy_revision': policy['editorial_policy_revision'], 'attempt': 1,
                  'dispatch_authorization': dispatch, 'format_requirements': 'Write the analysis in German.'}
        payload = {'receipt': receipt.decode(), 'brief': brief.decode(), 'config': config}
        script = _adapter_path(workspace, policy, 'dispatcher')
        check = subprocess.run(['node', str(script)], input=json.dumps({**payload, 'prepare_only': True}),
                               text=True, capture_output=True, timeout=15, cwd=workspace)
        if check.returncode:
            return {'status': 'preflight_failed', 'detail': json.loads(check.stdout)}
        # A unique owner makes concurrent same-key Root Writer requests conflict:
        # only the winner may call the provider. Existing attempts never auto-retry.
        request = {'dispatch': dispatch, 'policy': policy, 'pack_version': pack['version'],
                   'brief': pointer, 'owner': str(uuid.uuid4())}
        service._write_record(profile_path, profile, run_id, 'synthesis-request', base / 'request-r1.json', request)
        env = {**os.environ, 'OPENAI_API_KEY': api_key}
        try:
            call = subprocess.run(['node', str(script)], input=json.dumps(payload), text=True,
                                  capture_output=True, timeout=110, cwd=workspace, env=env)
            result = json.loads(call.stdout)
        except (subprocess.TimeoutExpired, ValueError):
            result = {'status': 'ambiguous', 'error': 'dispatch_outcome_unknown_no_retry'}
        service._write_record(profile_path, profile, run_id, 'synthesis-result', base / 'result-r1.json', result)
        return {'run_id': run_id, 'status': result['status'], 'usage': result.get('usage'),
                'result_record': (base / 'result-r1.json').as_posix()}
    finally:
        service.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path.cwd())
    parser.add_argument('--key-file', type=Path, help='Optional local key file; otherwise use OPENAI_API_KEY from the process environment')
    parser.add_argument('--project', required=True)
    args = parser.parse_args()
    try:
        result = synthesize(args.workspace.resolve(), args.key_file, args.project)
        print(json.dumps(result))
        raise SystemExit(0 if result['status'] == 'completed' else 1)
    except Exception:
        print(json.dumps({'status': 'failed', 'error': 'local_synthesis_failed_no_retry'}))
        raise SystemExit(1)
