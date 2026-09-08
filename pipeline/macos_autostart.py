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
import shutil
import stat
import subprocess
import sys
from typing import Callable

from pipeline import root_writer
from pipeline.feed_intake import _load_feeds
from pipeline.goal_loop import GoalLoopService


LABEL = "com.growth-clockwork.desk"
PLIST_NAME = f"{LABEL}.plist"
RESEARCH_LABEL = "com.growth-clockwork.weekly-research"
RESEARCH_PLIST_NAME = f"{RESEARCH_LABEL}.plist"
CHATGPT_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex")


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


def _codex_executable(value: Path | None = None) -> Path:
    discovered = value
    if discovered is None:
        discovered = shutil.which("codex")
    if discovered is None and sys.platform == "darwin" and CHATGPT_CODEX.is_file():
        discovered = CHATGPT_CODEX
    if discovered is None:
        raise AutostartError("Codex executable is required by enabled automation")
    return _regular(Path(discovered), "Codex executable", allow_symlink=True)


def launch_agent(
    workspace: Path, *, python_executable: Path | None = None,
    codex_executable: Path | None = None,
) -> bytes:
    workspace = root_writer._validate_workspace(Path(workspace))
    static_root = workspace / "dashboard/dist"
    _regular(static_root / "index.html", "Built Desk")
    executable = _regular(
        Path(sys.executable) if python_executable is None else Path(python_executable),
        "Python executable", allow_symlink=True,
    )
    broker_database = _regular(
        workspace / "runtime/broker/tasks.sqlite", "Broker database")
    broker_permissions = _regular(
        workspace / "runtime/permissions.json", "Broker permissions")
    for path, label in (
        (broker_database, "Broker database"),
        (broker_permissions, "Broker permissions"),
    ):
        if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise AutostartError(f"{label} must be private to the current user")
    try:
        permissions = json.loads(broker_permissions.read_text(encoding="utf-8"))
        automation_enabled = all(
            permissions["agents"][agent].get("autostart") is True
            for agent in ("research", "marketing", "mavery-qa")
        )
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        automation_enabled = False
    codex = None
    if automation_enabled:
        codex = _codex_executable(codex_executable)
    log_root = workspace / "runtime/service"
    arguments = [
        str(executable), "-m", "pipeline.review_api",
        "--workspace", str(workspace),
        "--port", "4173",
        "--static-root", str(static_root),
        "--broker-database", str(broker_database),
        "--broker-permissions", str(broker_permissions),
    ]
    if codex is not None:
        arguments.extend(("--codex-executable", str(codex)))
    value = {
        "Label": LABEL,
        "ProgramArguments": arguments,
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


def research_launch_agent(
    workspace: Path, project_id: str, *, python_executable: Path | None = None,
    codex_executable: Path | None = None,
) -> bytes:
    """Build one bounded weekly feed-intake job for an existing project."""

    workspace = root_writer._validate_workspace(Path(workspace))
    executable = _regular(
        Path(sys.executable) if python_executable is None else Path(python_executable),
        "Python executable", allow_symlink=True,
    )
    codex = _codex_executable(codex_executable)
    service = GoalLoopService(workspace)
    try:
        profile_path, _profile = service._selected(project_id)
        if not _load_feeds(profile_path):
            raise AutostartError("Selected project has no research feeds")
    except AutostartError:
        raise
    except (OSError, ValueError, KeyError) as exc:
        raise AutostartError("Selected research project is unavailable") from exc
    finally:
        service.close()
    log_root = workspace / "runtime/service"
    value = {
        "Label": RESEARCH_LABEL,
        "ProgramArguments": [
            str(executable), "-m", "pipeline.weekly_cycle",
            "--workspace", str(workspace),
            "--project", project_id,
            "--codex-executable", str(codex),
        ],
        "WorkingDirectory": str(workspace),
        "EnvironmentVariables": {
            "PYTHONPATH": str(workspace),
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        },
        # Catch up after a reboot. feed_intake reuses the current week's receipt.
        "RunAtLoad": True,
        "StartCalendarInterval": {"Weekday": 1, "Hour": 9, "Minute": 0},
        "ProcessType": "Background",
        "StandardOutPath": str(log_root / "weekly-research.log"),
        "StandardErrorPath": str(log_root / "weekly-research-error.log"),
    }
    return plistlib.dumps(value, fmt=plistlib.FMT_XML, sort_keys=True)


def _replace_private(target: Path, content: bytes) -> None:
    temporary = target.with_name(target.name + ".update")
    if temporary.exists() or temporary.is_symlink():
        raise AutostartError("LaunchAgent update file already exists")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
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
        temporary.unlink(missing_ok=True)
        raise AutostartError("LaunchAgent could not be written") from None
    else:
        os.close(descriptor)
    os.replace(temporary, target)
    os.chmod(target, 0o600)


def install(
    workspace: Path, *, launch_agents: Path | None = None,
    python_executable: Path | None = None, codex_executable: Path | None = None,
    uid: int | None = None, update: bool = False,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    platform: str = sys.platform,
) -> dict[str, object]:
    if platform != "darwin":
        raise AutostartError("macOS LaunchAgent installation is available only on macOS")
    workspace = root_writer._validate_workspace(Path(workspace))
    content = launch_agent(
        workspace, python_executable=python_executable,
        codex_executable=codex_executable,
    )
    return _install_agent(
        workspace, content, label=LABEL, plist_name=PLIST_NAME,
        service_name="Desk", launch_agents=launch_agents, uid=uid,
        update=update, run=run,
    )


def install_research(
    workspace: Path, project_id: str, *, launch_agents: Path | None = None,
    python_executable: Path | None = None, codex_executable: Path | None = None,
    uid: int | None = None,
    update: bool = False,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    platform: str = sys.platform,
) -> dict[str, object]:
    if platform != "darwin":
        raise AutostartError("macOS LaunchAgent installation is available only on macOS")
    workspace = root_writer._validate_workspace(Path(workspace))
    content = research_launch_agent(
        workspace, project_id, python_executable=python_executable,
        codex_executable=codex_executable)
    return _install_agent(
        workspace, content, label=RESEARCH_LABEL,
        plist_name=RESEARCH_PLIST_NAME, service_name="research schedule",
        launch_agents=launch_agents, uid=uid, update=update, run=run,
    )


def _install_agent(
    workspace: Path, content: bytes, *, label: str, plist_name: str,
    service_name: str, launch_agents: Path | None,
    uid: int | None, update: bool,
    run: Callable[..., subprocess.CompletedProcess],
) -> dict[str, object]:
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
    target = directory / plist_name
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise AutostartError("LaunchAgent target is unsafe")
    previous = target.read_bytes() if target.exists() else None
    if previous is not None:
        if previous != content and not update:
            raise AutostartError("A different Growth Clockwork LaunchAgent already exists")
        created = False
    else:
        _replace_private(target, content)
        created = True
    os.chmod(target, 0o600)
    domain = f"gui/{os.getuid() if uid is None else uid}"
    status = run(
        ["/bin/launchctl", "print", f"{domain}/{label}"],
        check=False, capture_output=True, text=True, timeout=10,
    )
    if status.returncode == 0 and previous == content:
        return {"status": "already_loaded", "label": label, "plist": str(target), "created": created}
    was_loaded = status.returncode == 0
    if was_loaded:
        stopped = run(
            ["/bin/launchctl", "bootout", f"{domain}/{label}"],
            check=False, capture_output=True, text=True, timeout=15,
        )
        if stopped.returncode != 0:
            raise AutostartError(f"launchctl could not stop the existing {service_name} service")
    if previous is not None and previous != content:
        _replace_private(target, content)
    loaded = run(
        ["/bin/launchctl", "bootstrap", domain, str(target)],
        check=False, capture_output=True, text=True, timeout=15,
    )
    if loaded.returncode != 0:
        if previous is not None and previous != content:
            _replace_private(target, previous)
            if was_loaded:
                run(
                    ["/bin/launchctl", "bootstrap", domain, str(target)],
                    check=False, capture_output=True, text=True, timeout=15,
                )
        elif created:
            target.unlink(missing_ok=True)
        raise AutostartError(f"launchctl could not load the {service_name} service")
    return {"status": "updated" if previous != content and previous is not None else "installed",
            "label": label, "plist": str(target), "created": created}


def readiness(
    workspace: Path, *, launch_agents: Path | None = None,
    python_executable: Path | None = None, codex_executable: Path | None = None,
    uid: int | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    platform: str = sys.platform,
) -> dict[str, str]:
    """Return one bounded state without installing, unloading or changing files."""

    if platform != "darwin":
        return {"state": "unsupported"}
    try:
        expected = launch_agent(
            workspace, python_executable=python_executable,
            codex_executable=codex_executable,
        )
    except (AutostartError, root_writer.WriterError):
        return {"state": "build_required"}
    return _readiness_for(
        expected, label=LABEL, plist_name=PLIST_NAME,
        launch_agents=launch_agents, uid=uid, run=run)


def research_readiness(
    workspace: Path, project_id: str, *, launch_agents: Path | None = None,
    python_executable: Path | None = None, codex_executable: Path | None = None,
    uid: int | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    platform: str = sys.platform,
) -> dict[str, str]:
    if platform != "darwin":
        return {"state": "unsupported"}
    try:
        expected = research_launch_agent(
            workspace, project_id, python_executable=python_executable,
            codex_executable=codex_executable)
    except (AutostartError, root_writer.WriterError):
        return {"state": "configuration_required"}
    return _readiness_for(
        expected, label=RESEARCH_LABEL, plist_name=RESEARCH_PLIST_NAME,
        launch_agents=launch_agents, uid=uid, run=run)


def _readiness_for(
    expected: bytes, *, label: str, plist_name: str,
    launch_agents: Path | None, uid: int | None,
    run: Callable[..., subprocess.CompletedProcess],
) -> dict[str, str]:
    raw_directory = (
        Path.home() / "Library/LaunchAgents" if launch_agents is None
        else Path(launch_agents)
    )
    if raw_directory.is_symlink() or not raw_directory.is_dir():
        return {"state": "needs_attention"}
    target = raw_directory.resolve() / plist_name
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
            ["/bin/launchctl", "print", f"{domain}/{label}"],
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
    parser.add_argument("--install-research", action="store_true",
                        help="Write and load the selected project's weekly research job")
    parser.add_argument("--project", help="Project id for --install-research")
    parser.add_argument("--update", action="store_true",
                        help="Safely replace this tool's existing LaunchAgent")
    args = parser.parse_args()
    if args.install and args.install_research:
        parser.error("choose either --install or --install-research")
    if args.update and not (args.install or args.install_research):
        parser.error("--update requires --install or --install-research")
    if args.install_research and not args.project:
        parser.error("--project is required with --install-research")
    try:
        if args.install_research:
            result = install_research(
                args.workspace, args.project, update=args.update)
        elif args.install:
            result = install(args.workspace, update=args.update)
        else:
            result = {"status": "preview", "plist": plistlib.loads(launch_agent(args.workspace))}
    except AutostartError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
