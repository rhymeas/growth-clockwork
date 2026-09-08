#!/usr/bin/env python3
"""Install the built loopback Desk as one macOS user LaunchAgent.

The agent serves existing ``dashboard/dist`` bytes. It never opens a remote bind,
stores a credential, builds source, or changes publish permissions.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
from typing import Callable

from pipeline import root_writer


LABEL = "com.growth-clockwork.desk"
PLIST_NAME = f"{LABEL}.plist"


class AutostartError(ValueError):
    pass


def _regular(path: Path, label: str, *, allow_symlink: bool = False) -> Path:
    original = Path(path)
    try:
        if original.is_symlink() and not allow_symlink:
            raise OSError()
        path = original.resolve()
        info = path.stat()
    except OSError:
        raise AutostartError(f"{label} is unavailable") from None
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise AutostartError(f"{label} must be a regular file")
    return path


def launch_agent(workspace: Path, *, python_executable: Path | None = None) -> bytes:
    workspace = root_writer._validate_workspace(Path(workspace))
    static_root = workspace / "dashboard/dist"
    _regular(static_root / "index.html", "Built Desk")
    executable = _regular(
        Path(sys.executable) if python_executable is None else Path(python_executable),
        "Python executable", allow_symlink=True,
    )
    log_root = workspace / "runtime/service"
    value = {
        "Label": LABEL,
        "ProgramArguments": [
            str(executable), "-m", "pipeline.review_api",
            "--workspace", str(workspace),
            "--port", "4173",
            "--static-root", str(static_root),
        ],
        "WorkingDirectory": str(workspace),
        "EnvironmentVariables": {
            "PYTHONPATH": str(workspace),
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "ThrottleInterval": 10,
        "StandardOutPath": str(log_root / "desk.log"),
        "StandardErrorPath": str(log_root / "desk-error.log"),
    }
    return plistlib.dumps(value, fmt=plistlib.FMT_XML, sort_keys=True)


def install(
    workspace: Path, *, launch_agents: Path | None = None,
    python_executable: Path | None = None, uid: int | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    platform: str = sys.platform,
) -> dict[str, object]:
    if platform != "darwin":
        raise AutostartError("macOS LaunchAgent installation is available only on macOS")
    workspace = root_writer._validate_workspace(Path(workspace))
    content = launch_agent(workspace, python_executable=python_executable)
    raw_directory = (Path.home() / "Library/LaunchAgents" if launch_agents is None
                     else Path(launch_agents))
    if raw_directory.is_symlink():
        raise AutostartError("LaunchAgents directory is unavailable or unsafe")
    directory = raw_directory.resolve()
    if not directory.is_dir():
        raise AutostartError("LaunchAgents directory is unavailable or unsafe")
    log_root = workspace / "runtime/service"
    if log_root.exists():
        if log_root.is_symlink() or not log_root.is_dir():
            raise AutostartError("Service log directory is unsafe")
    else:
        log_root.mkdir(mode=0o700)
    os.chmod(log_root, 0o700)
    target = directory / PLIST_NAME
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise AutostartError("LaunchAgent target is unsafe")
    if target.exists():
        if target.read_bytes() != content:
            raise AutostartError("A different Growth Clockwork LaunchAgent already exists")
        created = False
    else:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written < 1:
                    raise OSError("incomplete LaunchAgent write")
                view = view[written:]
            os.fsync(descriptor)
        except OSError:
            os.close(descriptor)
            target.unlink(missing_ok=True)
            raise AutostartError("LaunchAgent could not be written") from None
        else:
            os.close(descriptor)
        created = True
    os.chmod(target, 0o600)
    domain = f"gui/{os.getuid() if uid is None else uid}"
    status = run(
        ["/bin/launchctl", "print", f"{domain}/{LABEL}"],
        check=False, capture_output=True, text=True, timeout=10,
    )
    if status.returncode == 0:
        return {"status": "already_loaded", "label": LABEL, "plist": str(target), "created": created}
    loaded = run(
        ["/bin/launchctl", "bootstrap", domain, str(target)],
        check=False, capture_output=True, text=True, timeout=15,
    )
    if loaded.returncode != 0:
        if created:
            target.unlink(missing_ok=True)
        raise AutostartError("launchctl could not load the Desk service")
    return {"status": "installed", "label": LABEL, "plist": str(target), "created": created}


def readiness(
    workspace: Path, *, launch_agents: Path | None = None,
    python_executable: Path | None = None, uid: int | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    platform: str = sys.platform,
) -> dict[str, str]:
    """Return one bounded state without installing, unloading or changing files."""

    if platform != "darwin":
        return {"state": "unsupported"}
    try:
        expected = launch_agent(workspace, python_executable=python_executable)
    except (AutostartError, root_writer.WriterError):
        return {"state": "build_required"}
    raw_directory = (
        Path.home() / "Library/LaunchAgents" if launch_agents is None
        else Path(launch_agents)
    )
    if raw_directory.is_symlink() or not raw_directory.is_dir():
        return {"state": "needs_attention"}
    target = raw_directory.resolve() / PLIST_NAME
    if not target.exists():
        return {"state": "not_installed"}
    try:
        info = target.lstat()
        if (target.is_symlink() or not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077 or target.read_bytes() != expected):
            return {"state": "needs_attention"}
    except OSError:
        return {"state": "needs_attention"}
    domain = f"gui/{os.getuid() if uid is None else uid}"
    try:
        status = run(
            ["/bin/launchctl", "print", f"{domain}/{LABEL}"],
            check=False, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return {"state": "needs_attention"}
    return {"state": "running" if status.returncode == 0 else "installed_not_running"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--install", action="store_true",
                        help="Write and load the current user's LaunchAgent")
    args = parser.parse_args()
    try:
        if args.install:
            result = install(args.workspace)
        else:
            result = {"status": "preview", "plist": plistlib.loads(launch_agent(args.workspace))}
    except AutostartError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
