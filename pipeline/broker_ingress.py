"""Strict trusted-event admission. No HTTP listener, scheduler or model launch."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

from pipeline import root_writer
from pipeline.task_broker import BrokerError, TaskBroker, canonical


class IngressError(BrokerError):
    pass


COMMON_FIELDS = {
    'version', 'project_id', 'agent_id', 'origin', 'actor_id', 'input_ref',
    'input_sha256', 'not_before', 'max_attempts', 'identity',
}
IDENTITY_FIELDS = {
    'operator': {'transport', 'account_id', 'event_id'},
    'connector': {'connector_id', 'account_id', 'event_id'},
    'schedule': {'schedule_id', 'scheduled_for'},
    'agent': {'parent_task_id', 'event_id'},
}
PERSIST_FIELDS = COMMON_FIELDS - {'input_ref', 'input_sha256'}


def _text(value, label, *, maximum=2048):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise IngressError(f'Invalid {label}')
    return value


def _identity(envelope):
    origin = envelope['origin']
    identity = envelope['identity']
    required = IDENTITY_FIELDS[origin]
    if not isinstance(identity, dict) or set(identity) != required:
        raise IngressError('Invalid ingress identity')
    for field in required:
        if field == 'scheduled_for':
            if type(identity[field]) is not int or identity[field] < 0:
                raise IngressError('Invalid scheduled occurrence')
        else:
            _text(identity[field], f'ingress identity {field}')
    # Store no raw channel/account/message identity in the task row.
    return hashlib.sha256(canonical([origin, identity]).encode()).hexdigest()


def admit_event(broker, envelope):
    """Admit one server-stamped event idempotently; never trust prompt contents.

    Caller must construct this envelope from typed host/runtime facts. This function
    does not authenticate a caller and must never be exposed directly to clients.
    """
    if not isinstance(envelope, dict) or set(envelope) != COMMON_FIELDS:
        raise IngressError('Invalid ingress envelope')
    if envelope['version'] != 1 or envelope['origin'] not in IDENTITY_FIELDS:
        raise IngressError('Unsupported ingress envelope')
    project = _text(envelope['project_id'], 'project')
    agent = _text(envelope['agent_id'], 'agent')
    actor = _text(envelope['actor_id'], 'actor')
    input_ref = _text(envelope['input_ref'], 'input reference')
    digest = envelope['input_sha256']
    if (not isinstance(digest, str) or len(digest) != 64
            or any(char not in '0123456789abcdef' for char in digest)):
        raise IngressError('Invalid input hash')
    if (type(envelope['not_before']) is not int or envelope['not_before'] < 0
            or type(envelope['max_attempts']) is not int
            or not 1 <= envelope['max_attempts'] <= 5):
        raise IngressError('Invalid ingress counters')
    identity = envelope['identity']
    parent = identity.get('parent_task_id') if envelope['origin'] == 'agent' else None
    return broker.admit(
        project=project, origin=envelope['origin'], origin_key=_identity(envelope),
        agent=agent, input_ref=input_ref, input_sha256=digest, actor=actor,
        not_before=envelope['not_before'], max_attempts=envelope['max_attempts'],
        parent_task_id=parent,
    )


def persist_and_admit_event(broker, *, workspace, profile_path, envelope, input_text):
    """Root-Writer-persist exact input, then admit its hash; never starts a worker."""
    if (not isinstance(envelope, dict) or set(envelope) != PERSIST_FIELDS
            or not isinstance(input_text, str) or not input_text.strip()
            or len(input_text.encode('utf-8')) > 64000):
        raise IngressError('Invalid persisted ingress request')
    workspace = Path(workspace)
    profile_path = Path(profile_path)
    profile = root_writer.load_project_profile(profile_path)
    if profile['project_id'] != envelope.get('project_id'):
        raise IngressError('Ingress profile does not match project')

    origin_key = _identity(envelope)
    input_ref = f'records/ingress/{origin_key}/r1.txt'
    content_sha256 = hashlib.sha256(input_text.encode('utf-8')).hexdigest()
    idempotency_key = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f'growth-clockwork-ingress:{profile["project_id"]}:{origin_key}',
    ))
    request = {
        'writer_request_version': '1.0',
        'project_id': profile['project_id'],
        'project_profile_revision': profile['profile_revision'],
        'run_id': f'ingress-{origin_key[:32]}',
        'idempotency_key': idempotency_key,
        'requested_by': profile['root_role_id'],
        'writes': [{
            'path': input_ref, 'mode': 'create', 'content': input_text,
            'content_sha256': content_sha256, 'expected_sha256': None,
        }],
    }
    receipt = root_writer.apply_request_value(workspace, profile_path, request)
    expected = next(
        (item for item in receipt['writes'] if item['path'] == input_ref), None
    )
    if expected is None or expected['sha256'] != content_sha256:
        raise IngressError('Writer receipt does not verify ingress input')

    admitted = admit_event(broker, envelope | {
        'input_ref': input_ref,
        'input_sha256': content_sha256,
    })
    return {'task': admitted, 'input': {
        'artifact_ref': input_ref, 'artifact_sha256': content_sha256,
        'writer_receipt_key': receipt['idempotency_key'],
        'writer_request_sha256': receipt['request_sha256'],
    }}


def main(argv=None, *, stdin=None, stdout=None, stderr=None):
    """One-shot local process boundary for a future trusted harness adapter."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--permissions', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--profile', required=True, type=Path)
    args = parser.parse_args(argv)
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    try:
        raw = stdin.read(131073)
        if isinstance(raw, str):
            raw = raw.encode('utf-8')
        if len(raw) > 131072:
            raise IngressError('Ingress request is too large')
        request = json.loads(raw.decode('utf-8'))
        if not isinstance(request, dict) or set(request) != {'event', 'input_text'}:
            raise IngressError('Invalid ingress process request')
        broker = TaskBroker(args.database, args.permissions)
        try:
            result = persist_and_admit_event(
                broker, workspace=args.workspace, profile_path=args.profile,
                envelope=request['event'], input_text=request['input_text'])
        finally:
            broker.close()
        safe = {
            'task_id': result['task']['task_id'],
            'status': result['task']['status'],
            'project_id': result['task']['project_id'],
            'agent_id': result['task']['agent_id'],
            'input': result['input'],
        }
        stdout.write(json.dumps(safe, sort_keys=True) + '\n')
        return 0
    except (IngressError, BrokerError, root_writer.WriterError, OSError,
            UnicodeError, json.JSONDecodeError) as error:
        stderr.write(f'ingress rejected: {error}\n')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
