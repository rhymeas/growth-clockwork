"""Generate a stable CycloneDX SBOM from the locked dashboard dependency tree."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess


class SbomError(ValueError):
    pass


def normalize_sbom(payload: bytes) -> bytes:
    try:
        document = json.loads(payload)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise SbomError('npm returned an invalid SBOM') from exc
    if (not isinstance(document, dict) or document.get('bomFormat') != 'CycloneDX'
            or not isinstance(document.get('components'), list)
            or not isinstance(document.get('dependencies'), list)):
        raise SbomError('npm returned an unsupported SBOM')
    document.pop('serialNumber', None)
    metadata = document.get('metadata')
    if isinstance(metadata, dict):
        metadata.pop('timestamp', None)
        metadata.pop('tools', None)
    document['components'].sort(key=lambda item: item.get('bom-ref', ''))
    for dependency in document['dependencies']:
        if isinstance(dependency, dict) and isinstance(dependency.get('dependsOn'), list):
            dependency['dependsOn'].sort()
    document['dependencies'].sort(key=lambda item: item.get('ref', ''))
    return (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + '\n').encode('utf-8')


def generate_sbom(workspace: Path) -> bytes:
    workspace = Path(workspace).resolve()
    dashboard = workspace / 'dashboard'
    if dashboard.is_symlink() or not (dashboard / 'package-lock.json').is_file():
        raise SbomError('Dashboard dependency lock is unavailable')
    try:
        process = subprocess.run(
            ['npm', 'sbom', '--package-lock-only', '--sbom-format', 'cyclonedx'],
            cwd=dashboard, check=False, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SbomError('npm sbom could not run') from exc
    if process.returncode != 0:
        raise SbomError('npm sbom failed')
    return normalize_sbom(process.stdout)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.write_bytes(generate_sbom(args.workspace))
    print(f'CycloneDX SBOM: {output}')
