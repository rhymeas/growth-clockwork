"""Deterministic Inbox router for Studio proposals. No model or publication."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import uuid

from pipeline import root_writer, studio
from pipeline.task_broker import BrokerError, TaskBroker


class InboxError(BrokerError):
    pass


def _read_proposal(broker: TaskBroker, *, workspace: Path, profile: dict,
                   task: dict) -> dict:
    if task['agent_id'] != 'inbox' or task['status'] != 'queued':
        raise InboxError('Inbox task is not ready')
    reference = PurePosixPath(task['input_ref'])
    if (reference.is_absolute() or '..' in reference.parts
            or len(reference.parts) != 3 or reference.parts[:2] != ('records', 'studio')
            or not reference.name.endswith('.json')):
        raise InboxError('Inbox input is not a Studio proposal')
    admitted = broker.db.execute('''SELECT details_json FROM audit_events
        WHERE project_id=? AND task_id=? AND event_type='admitted' ''',
        (task['project_id'], task['task_id'])).fetchone()
    if admitted is None:
        raise InboxError('Inbox task lacks admission proof')
    expected = json.loads(admitted[0]).get('input_sha256')
    try:
        with root_writer._project_state_fd(workspace, profile['_state_root']) as state_fd:
            content = root_writer._read_relative_bytes(state_fd, reference)
        if hashlib.sha256(content).hexdigest() != expected:
            raise InboxError('Inbox input hash changed')
        record = json.loads(content)
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, InboxError):
            raise
        raise InboxError('Inbox input cannot be verified') from exc
    if (not isinstance(record, dict)
            or set(record) != {'version', 'id', 'project_id', 'project_profile_revision',
                               'request_sha256', 'created_at', 'kind', 'payload'}
            or record['version'] != '1.0' or record['kind'] != 'proposal'
            or record['project_id'] != task['project_id']
            or record['project_profile_revision'] != profile['profile_revision']
            or reference.name != f"{record['id']}.json"):
        raise InboxError('Inbox input is not a valid proposal')
    payload = record['payload']
    if (not isinstance(payload, dict)
            or set(payload) != {'title', 'channel', 'audience_id', 'material_ids', 'status'}
            or payload['status'] != 'draft' or payload['channel'] not in studio.CONTENT_CHANNELS
            or not isinstance(payload['title'], str) or not payload['title'].strip()
            or (payload['audience_id'] is not None and not isinstance(payload['audience_id'], str))
            or not isinstance(payload['material_ids'], list)
            or any(not isinstance(item, str) for item in payload['material_ids'])):
        raise InboxError('Inbox proposal fields are invalid')
    return record


def route_task(broker: TaskBroker, *, project: str, task_id: str,
               workspace: Path, profile_path: Path, lease_seconds: int = 60) -> dict:
    """Route one queued Studio proposal to Research with an exact linked handoff."""
    workspace = root_writer._validate_workspace(Path(workspace))
    profile_path = Path(profile_path)
    profile = root_writer.load_project_profile(profile_path)
    root_writer._validate_profile_package(workspace, profile_path, profile)
    if profile['project_id'] != project:
        raise InboxError('Inbox profile does not match task project')
    task = broker.get(project, task_id)
    record = _read_proposal(broker, workspace=workspace, profile=profile, task=task)
    # Deny before claiming/writing if downstream execution is not currently granted.
    broker.permissions('research')
    payload = record['payload']
    handoff = {
        'inbox_handoff_version': '1.0',
        'project_id': project,
        'parent_task_id': task_id,
        'route': 'content-channel',
        'goal': payload['title'].strip(),
        'channel': payload['channel'],
        'audience_id': payload['audience_id'],
        'material_ids': payload['material_ids'],
        'source': {'kind': 'studio-proposal', 'record_id': record['id']},
    }
    content = json.dumps(handoff, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'))
    digest = hashlib.sha256(content.encode()).hexdigest()
    output_ref = f'records/inbox-handoffs/{task_id}/r1.json'
    request = {
        'writer_request_version': '1.0',
        'project_id': project,
        'project_profile_revision': profile['profile_revision'],
        'run_id': task_id,
        'idempotency_key': str(uuid.uuid5(uuid.NAMESPACE_URL,
            f'growth-inbox-handoff:{project}:{task_id}:r1')),
        'requested_by': profile['root_role_id'],
        'writes': [{
            'path': output_ref, 'mode': 'create', 'content': content,
            'content_sha256': digest, 'expected_sha256': None,
            'media_type': 'application/json',
        }],
    }
    receipt = root_writer.apply_request_value(workspace, profile_path, request)
    written = next((item for item in receipt['writes']
                    if item['path'] == output_ref), None)
    if written is None or written['sha256'] != digest:
        raise InboxError('Inbox handoff receipt does not match output')
    claim = broker.claim(project, task_id, agent='inbox', lease_seconds=lease_seconds)
    try:
        origin_key = hashlib.sha256(json.dumps(
            ['agent', {'parent_task_id': task_id, 'event_id': 'research-handoff-r1'}],
            sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        result = broker.delegate(
            project, task_id, agent='inbox', token=claim['lease_token'],
            version=claim['version'], child_agent='research',
            origin_key=origin_key, input_ref=output_ref, input_sha256=digest)
        return {'inbox_task_id': result['parent']['task_id'],
                'inbox_status': result['parent']['status'],
                'research_task_id': result['child']['task_id'],
                'research_status': result['child']['status'],
                'handoff_ref': output_ref, 'handoff_sha256': digest}
    except Exception:
        try:
            broker.fail(project, task_id, agent='inbox', token=claim['lease_token'],
                        version=claim['version'])
        except BrokerError:
            pass
        raise
