"""Read-only technical audit for a clean Growth Clockwork export candidate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re


class PackageAuditError(ValueError):
    pass


ALLOWED_DEPENDENCY_LICENSES = {
    'Apache-2.0', 'BSD-2-Clause', 'BSD-3-Clause', 'ISC', 'MIT', 'MIT-0', 'MPL-2.0',
}

SECRET_PATTERNS = {
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'openai_key': re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{40,}'),
    'github_token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})'),
    'google_api_key': re.compile(r'\bAIza[0-9A-Za-z_-]{30,}'),
    'slack_token': re.compile(r'\bxox[baprs]-[0-9A-Za-z-]{20,}'),
    'bearer_header': re.compile(r'Authorization\s*:\s*Bearer\s+[A-Za-z0-9._~-]{20,}', re.I),
}

MEDIA_SUFFIXES = {
    '.avif', '.gif', '.ico', '.jpeg', '.jpg', '.mov', '.mp3', '.mp4', '.otf',
    '.pdf', '.png', '.svg', '.ttf', '.wav', '.webm', '.webp', '.woff', '.woff2',
}


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or '..' in path.parts or '\\' in value
            or path.as_posix() != value):
        raise PackageAuditError('Export receipt contains an unsafe path')
    return path


def scan_text(relative: PurePosixPath, content: str) -> list[str]:
    findings = []
    # Build the macOS home prefix in pieces so this scanner does not flag itself.
    if re.search('/' + r'Users/[^/\s]+/', content):
        findings.append(f'{relative}: absolute_user_path')
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(content):
            findings.append(f'{relative}: {label}')
    return findings


def audit_candidate(candidate: Path) -> dict:
    candidate = Path(candidate).resolve()
    if candidate.is_symlink() or not candidate.is_dir():
        raise PackageAuditError('Candidate must be a real directory')
    receipt_path = candidate / 'EXPORT-RECEIPT.json'
    try:
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise PackageAuditError('Candidate receipt is unavailable or invalid') from exc
    if (not isinstance(receipt, dict)
            or receipt.get('status') != 'licensed_export_candidate'
            or receipt.get('published') is not False
            or receipt.get('license_granted') is not True
            or receipt.get('license') != 'Apache-2.0'
            or not isinstance(receipt.get('files'), list)):
        raise PackageAuditError('Candidate receipt has an unsupported status')
    listed = {}
    for item in receipt['files']:
        if (not isinstance(item, dict)
                or not {'path', 'sha256', 'bytes'}.issubset(item)
                or not isinstance(item['path'], str)
                or not isinstance(item['sha256'], str)
                or not isinstance(item['bytes'], int)):
            raise PackageAuditError('Candidate receipt contains an invalid file')
        relative = _safe_relative(item['path'])
        if relative in listed:
            raise PackageAuditError('Candidate receipt contains duplicate paths')
        listed[relative] = item
    git_directory = candidate / '.git'
    if ((git_directory.exists() or git_directory.is_symlink())
            and (git_directory.is_symlink() or not git_directory.is_dir())):
        raise PackageAuditError('Candidate Git history must be a real directory')
    actual = set()
    text_findings = []
    for path in candidate.rglob('*'):
        try:
            relative = PurePosixPath(path.relative_to(candidate).as_posix())
        except ValueError:
            raise PackageAuditError('Candidate path escapes its root') from None
        if '.git' in relative.parts:
            continue
        if path.is_symlink():
            raise PackageAuditError('Candidate contains a symlink')
        if not path.is_file() or path == receipt_path:
            continue
        # Running this module may create local bytecode after export. It is not
        # package content and was already forbidden by the exporter.
        if '__pycache__' in relative.parts or relative.suffix in ('.pyc', '.pyo'):
            continue
        actual.add(relative)
        item = listed.get(relative)
        if item is None:
            raise PackageAuditError(f'Unreceipted candidate file: {relative}')
        content = path.read_bytes()
        if (len(content) != item['bytes']
                or hashlib.sha256(content).hexdigest() != item['sha256']):
            raise PackageAuditError(f'Candidate file differs from receipt: {relative}')
        try:
            text_findings.extend(scan_text(relative, content.decode('utf-8')))
        except UnicodeError:
            pass
    if actual != set(listed):
        missing = sorted(str(path) for path in set(listed) - actual)
        raise PackageAuditError(f'Receipted candidate file is missing: {missing[0]}')
    if text_findings:
        raise PackageAuditError(f'Portable-text scan failed: {text_findings[0]}')
    bundled_media = sorted(str(path) for path in actual if path.suffix.lower() in MEDIA_SUFFIXES)
    if bundled_media:
        raise PackageAuditError(f'Bundled media requires provenance review: {bundled_media[0]}')
    required = {
        PurePosixPath('README.md'), PurePosixPath('LICENSE'),
        PurePosixPath('CONTRIBUTING.md'), PurePosixPath('SECURITY.md'),
        PurePosixPath('THIRD-PARTY.md'), PurePosixPath('SBOM.cdx.json'),
        PurePosixPath('.gitignore'), PurePosixPath('.gitleaks.toml'),
        PurePosixPath('runtime/permissions.example.json'),
    }
    missing_required = sorted(str(path) for path in required - actual)
    if missing_required:
        raise PackageAuditError(f'Required package file is missing: {missing_required[0]}')

    lock_path = candidate / 'dashboard/package-lock.json'
    try:
        lock = json.loads(lock_path.read_text(encoding='utf-8'))
        packages = lock['packages']
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise PackageAuditError('Dashboard dependency lock is unavailable or invalid') from exc
    if not isinstance(packages, dict):
        raise PackageAuditError('Dashboard dependency lock packages are invalid')
    license_counts = {}
    unresolved = []
    for package_path, package in packages.items():
        if not package_path:
            continue
        license_id = package.get('license') if isinstance(package, dict) else None
        if license_id not in ALLOWED_DEPENDENCY_LICENSES:
            unresolved.append({'package': package_path, 'license': license_id})
            continue
        license_counts[license_id] = license_counts.get(license_id, 0) + 1
    if unresolved:
        raise PackageAuditError(
            f'Dependency license requires review: {unresolved[0]["package"]}')
    try:
        sbom = json.loads((candidate / 'SBOM.cdx.json').read_text(encoding='utf-8'))
        components = sbom['components']
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise PackageAuditError('CycloneDX SBOM is unavailable or invalid') from exc
    if (sbom.get('bomFormat') != 'CycloneDX' or not isinstance(components, list)
            or 'serialNumber' in sbom or 'timestamp' in sbom.get('metadata', {})):
        raise PackageAuditError('CycloneDX SBOM is unsupported or not normalized')
    locked = {
        (package.get('name') or package_path.rsplit('node_modules/', 1)[-1], package.get('version'))
        for package_path, package in packages.items()
        if package_path and isinstance(package, dict)
    }
    described = {
        (component.get('name'), component.get('version'))
        for component in components if isinstance(component, dict)
    }
    if locked != described:
        raise PackageAuditError('CycloneDX SBOM does not match the dependency lock')
    return {
        'status': 'technical_audit_passed',
        'candidate_files': len(listed),
        'receipt_hashes': 'verified',
        'portable_text': 'no_high_confidence_secret_or_absolute_user_path_found',
        'bundled_media_files': 0,
        'dependency_license_counts': dict(sorted(license_counts.items())),
        'sbom': {'format': 'CycloneDX', 'components': len(components), 'lock_match': True},
        'license_granted': True,
        'license': 'Apache-2.0',
        'published': False,
        'limits': [
            'Pattern scan is not a legal or complete secret-history audit.',
            'MPL-2.0 dependencies need their own notices and file-level compliance.',
            'A clean repository history must be scanned separately before publication.',
        ],
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit_candidate(args.candidate), indent=2, sort_keys=True))
