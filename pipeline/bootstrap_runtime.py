"""Create the local broker and private permission file once. Never overwrite either."""
from __future__ import annotations

import json
import os
from pathlib import Path

from pipeline import root_writer
from pipeline.task_broker import TaskBroker


class BootstrapError(ValueError):
    pass


def _real_directory(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise BootstrapError(f'{label} must be a real directory')


def bootstrap(workspace: Path) -> dict:
    workspace = root_writer._validate_workspace(Path(workspace))
    runtime = workspace / 'runtime'
    if runtime.exists():
        _real_directory(runtime, 'runtime')
    else:
        runtime.mkdir(mode=0o700)
    example = runtime / 'permissions.example.json'
    if example.is_symlink() or not example.is_file():
        raise BootstrapError('runtime/permissions.example.json is unavailable')
    try:
        template = example.read_bytes()
        parsed = json.loads(template)
        if parsed.get('publish') != 'review' or not isinstance(parsed.get('agents'), dict):
            raise BootstrapError('Permission template is invalid')
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        if isinstance(exc, BootstrapError):
            raise
        raise BootstrapError('Permission template is invalid') from exc
    permissions = runtime / 'permissions.json'
    created_permissions = False
    if permissions.exists() or permissions.is_symlink():
        if permissions.is_symlink() or not permissions.is_file():
            raise BootstrapError('runtime/permissions.json must be a real file')
    else:
        try:
            descriptor = os.open(permissions, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                view = memoryview(template)
                while view:
                    written = os.write(descriptor, view)
                    if written < 1:
                        raise BootstrapError('Permission file write was incomplete')
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            created_permissions = True
        except FileExistsError:
            raise BootstrapError('Permission file appeared during setup') from None
        except OSError as exc:
            raise BootstrapError('Permission file could not be created') from exc
    broker_directory = runtime / 'broker'
    if broker_directory.exists():
        _real_directory(broker_directory, 'runtime/broker')
    else:
        broker_directory.mkdir(mode=0o700)
    database = broker_directory / 'tasks.sqlite'
    if database.is_symlink() or (database.exists() and not database.is_file()):
        raise BootstrapError('Broker database must be a real file')
    broker = TaskBroker(database, permissions)
    try:
        broker.permissions('inbox')
        broker.permissions('research')
        broker.permissions('marketing')
        broker.permissions('mavery-qa')
    finally:
        broker.close()
    try:
        os.chmod(permissions, 0o600)
        os.chmod(database, 0o600)
    except OSError as exc:
        raise BootstrapError('Runtime file permissions could not be restricted') from exc
    return {
        'status': 'ready',
        'permissions': 'runtime/permissions.json',
        'permissions_created': created_permissions,
        'database': 'runtime/broker/tasks.sqlite',
        'publish': 'review',
        'autostart': False,
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(bootstrap(args.workspace), indent=2, sort_keys=True))
