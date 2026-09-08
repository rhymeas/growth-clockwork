"""Audit a clean package repository, its full history, and its locked SBOM."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile

from pipeline.package_audit import PackageAuditError, audit_candidate, scan_text


class ReleaseGateError(ValueError):
    pass


EMAIL = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
PHONE = re.compile(r'(?<![\w.])\+[1-9][0-9 ()-]{8,18}[0-9](?![\w.])')
PROVENANCE_EMAIL_FILES = {
    PurePosixPath('dashboard/package-lock.json'), PurePosixPath('SBOM.cdx.json')}


def _git(repository: Path, arguments: list[str]) -> bytes:
    process = subprocess.run(
        ['git', *arguments], cwd=repository, check=False, capture_output=True, timeout=60)
    if process.returncode != 0:
        raise ReleaseGateError('Git history could not be inspected')
    return process.stdout


def scan_history_personal_data(repository: Path) -> dict:
    repository = Path(repository).resolve()
    if repository.is_symlink() or not repository.is_dir() or not (repository / '.git').exists():
        raise ReleaseGateError('Candidate must be a Git repository')
    if _git(repository, ['status', '--porcelain', '--untracked-files=all']).strip():
        raise ReleaseGateError('Candidate history scan requires a clean worktree')
    commits = _git(repository, ['rev-list', '--all']).decode().splitlines()
    if not commits:
        raise ReleaseGateError('Candidate has no commit history')
    findings = []
    inspected = set()
    for commit in commits:
        tree = _git(repository, ['ls-tree', '-r', '-z', '--full-tree', commit])
        for record in tree.split(b'\0'):
            if not record:
                continue
            try:
                metadata, raw_path = record.split(b'\t', 1)
                _, kind, blob = metadata.decode().split()
                relative = PurePosixPath(raw_path.decode())
            except (UnicodeError, ValueError):
                raise ReleaseGateError('Git tree contains an unsupported path') from None
            if kind != 'blob' or (blob, relative) in inspected:
                continue
            inspected.add((blob, relative))
            content = _git(repository, ['cat-file', 'blob', blob])
            if b'\0' in content:
                continue
            try:
                text = content.decode('utf-8')
            except UnicodeError:
                continue
            for finding in scan_text(relative, text):
                if finding.endswith('absolute_user_path'):
                    findings.append(finding)
            if relative not in PROVENANCE_EMAIL_FILES and EMAIL.search(text):
                findings.append(f'{relative}: email_address')
            if PHONE.search(text):
                findings.append(f'{relative}: international_phone_number')
    if findings:
        raise ReleaseGateError(f'Personal-data history scan failed: {findings[0]}')
    return {'commits': len(commits), 'text_blobs': len(inspected), 'findings': 0}


def release_gate(candidate: Path, gitleaks: str = 'gitleaks') -> dict:
    candidate = Path(candidate).resolve()
    try:
        package = audit_candidate(candidate)
    except PackageAuditError as exc:
        raise ReleaseGateError(str(exc)) from exc
    history = scan_history_personal_data(candidate)
    with tempfile.TemporaryDirectory() as directory:
        report = Path(directory) / 'gitleaks.json'
        try:
            process = subprocess.run([
                gitleaks, 'git', '--no-banner', '--redact=100', '--report-format', 'json',
                '--report-path', str(report), '--timeout', '60', '.'
            ], cwd=candidate, check=False, capture_output=True, text=True, timeout=75)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReleaseGateError('Gitleaks could not run') from exc
        if process.returncode != 0:
            count = 'unknown'
            try:
                count = str(len(json.loads(report.read_text())))
            except (OSError, ValueError, TypeError):
                pass
            raise ReleaseGateError(f'Gitleaks found {count} potential secret(s)')
    return {
        'status': 'local_release_gate_passed', 'published': False,
        'package': package, 'history': history,
        'gitleaks': {'status': 'passed', 'output_redacted': True},
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--gitleaks', default='gitleaks')
    args = parser.parse_args()
    print(json.dumps(release_gate(args.candidate, args.gitleaks), indent=2, sort_keys=True))
