from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

from pipeline import macos_autostart


class MacOSAutostartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name) / "workspace"
        self.workspace.mkdir()
        (self.workspace / "AGENTS.md").write_text("# Synthetic workspace\n", encoding="utf-8")
        (self.workspace / "dashboard/dist").mkdir(parents=True)
        (self.workspace / "dashboard/dist/index.html").write_text("built", encoding="utf-8")
        (self.workspace / "runtime").mkdir()
        (self.workspace / "runtime/broker").mkdir()
        (self.workspace / "runtime/broker/tasks.sqlite").write_bytes(b"")
        (self.workspace / "runtime/permissions.json").write_text(json.dumps({
            "agents": {
                "research": {"autostart": True},
                "marketing": {"autostart": True},
                "mavery-qa": {"autostart": True},
            }
        }), encoding="utf-8")
        os.chmod(self.workspace / "runtime/broker/tasks.sqlite", 0o600)
        os.chmod(self.workspace / "runtime/permissions.json", 0o600)
        self.launch_agents = Path(self.temporary.name) / "LaunchAgents"
        self.launch_agents.mkdir()
        self.python = Path(sys.executable).resolve()

    def test_generated_agent_is_loopback_built_and_contains_no_credentials(self) -> None:
        raw = macos_autostart.launch_agent(
            self.workspace, python_executable=self.python,
            codex_executable=self.python,
        )
        value = plistlib.loads(raw)
        self.assertEqual(value["Label"], "com.growth-clockwork.desk")
        arguments = value["ProgramArguments"]
        self.assertEqual(arguments[arguments.index("--port") + 1], "4173")
        self.assertEqual(arguments[arguments.index("--static-root") + 1], str((self.workspace / "dashboard/dist").resolve()))
        self.assertEqual(arguments[arguments.index("--broker-database") + 1], str((self.workspace / "runtime/broker/tasks.sqlite").resolve()))
        self.assertEqual(arguments[arguments.index("--broker-permissions") + 1], str((self.workspace / "runtime/permissions.json").resolve()))
        self.assertEqual(arguments[arguments.index("--codex-executable") + 1], str(self.python))
        self.assertNotIn("--host", arguments)
        self.assertNotIn("token", raw.decode().lower())
        self.assertNotIn("password", raw.decode().lower())

    def test_install_writes_mode_600_and_bootstraps_user_domain(self) -> None:
        calls: list[list[str]] = []

        def run(command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 1 if command[1] == "print" else 0, "", "")

        result = macos_autostart.install(
            self.workspace, launch_agents=self.launch_agents,
            python_executable=self.python, codex_executable=self.python,
            uid=501, run=run, platform="darwin",
        )
        target = self.launch_agents / macos_autostart.PLIST_NAME
        self.assertEqual(result["status"], "installed")
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(calls[0][2], "gui/501/com.growth-clockwork.desk")
        self.assertEqual(calls[1], ["/bin/launchctl", "bootstrap", "gui/501", str(target.resolve())])
        self.assertEqual((self.workspace / "runtime/service").stat().st_mode & 0o777, 0o700)

    def test_existing_different_agent_and_failed_bootstrap_fail_closed(self) -> None:
        target = self.launch_agents / macos_autostart.PLIST_NAME
        target.write_text("different", encoding="utf-8")
        with self.assertRaisesRegex(macos_autostart.AutostartError, "different"):
            macos_autostart.install(
                self.workspace, launch_agents=self.launch_agents,
                python_executable=self.python, codex_executable=self.python,
                run=lambda *_args, **_kwargs: None,
                platform="darwin",
            )
        target.unlink()

        def fail(command, **_kwargs):
            return subprocess.CompletedProcess(command, 1, "", "")

        with self.assertRaisesRegex(macos_autostart.AutostartError, "could not load"):
            macos_autostart.install(
                self.workspace, launch_agents=self.launch_agents,
                python_executable=self.python, codex_executable=self.python,
                run=fail, platform="darwin",
            )
        self.assertFalse(target.exists())

    def test_update_replaces_and_reloads_only_the_owned_agent(self) -> None:
        target = self.launch_agents / macos_autostart.PLIST_NAME
        target.write_text("older owned configuration", encoding="utf-8")
        calls = []

        def run(command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        result = macos_autostart.install(
            self.workspace, launch_agents=self.launch_agents,
            python_executable=self.python, codex_executable=self.python,
            uid=501, run=run, platform="darwin", update=True,
        )
        self.assertEqual(result["status"], "updated")
        self.assertEqual(target.read_bytes(), macos_autostart.launch_agent(
            self.workspace, python_executable=self.python,
            codex_executable=self.python))
        self.assertEqual(calls[0][1:3], ["print", "gui/501/com.growth-clockwork.desk"])
        self.assertEqual(calls[1][1:3], ["bootout", "gui/501/com.growth-clockwork.desk"])
        self.assertEqual(calls[2][1:3], ["bootstrap", "gui/501"])

    def test_failed_update_restores_previous_agent_and_reload_attempt(self) -> None:
        target = self.launch_agents / macos_autostart.PLIST_NAME
        previous = b"older owned configuration"
        target.write_bytes(previous)
        calls = []
        bootstrap_count = 0

        def run(command, **_kwargs):
            nonlocal bootstrap_count
            calls.append(command)
            if command[1] == "bootstrap":
                bootstrap_count += 1
                code = 1 if bootstrap_count == 1 else 0
            else:
                code = 0
            return subprocess.CompletedProcess(command, code, "", "")

        with self.assertRaisesRegex(macos_autostart.AutostartError, "could not load"):
            macos_autostart.install(
                self.workspace, launch_agents=self.launch_agents,
                python_executable=self.python, codex_executable=self.python,
                uid=501, run=run, platform="darwin", update=True,
            )
        self.assertEqual(target.read_bytes(), previous)
        self.assertEqual(bootstrap_count, 2)
        self.assertFalse(target.with_name(target.name + ".update").exists())

    def test_non_macos_and_missing_build_are_rejected_without_write(self) -> None:
        with self.assertRaisesRegex(macos_autostart.AutostartError, "only on macOS"):
            macos_autostart.install(
                self.workspace, launch_agents=self.launch_agents,
                python_executable=self.python, platform="linux",
            )
        (self.workspace / "dashboard/dist/index.html").unlink()
        with self.assertRaisesRegex(macos_autostart.AutostartError, "Built Desk"):
            macos_autostart.launch_agent(
                self.workspace, python_executable=self.python,
                codex_executable=self.python)
        self.assertEqual(list(self.launch_agents.iterdir()), [])

    def test_missing_or_exposed_broker_state_is_rejected(self) -> None:
        database = self.workspace / "runtime/broker/tasks.sqlite"
        database.unlink()
        with self.assertRaisesRegex(macos_autostart.AutostartError, "Broker database"):
            macos_autostart.launch_agent(
                self.workspace, python_executable=self.python,
                codex_executable=self.python)
        database.write_bytes(b"")
        os.chmod(database, 0o644)
        with self.assertRaisesRegex(macos_autostart.AutostartError, "private"):
            macos_autostart.launch_agent(
                self.workspace, python_executable=self.python,
                codex_executable=self.python)

    def test_enabled_agent_autostart_requires_a_codex_executable(self) -> None:
        with self.assertRaisesRegex(macos_autostart.AutostartError, "Codex executable"):
            macos_autostart.launch_agent(
                self.workspace, python_executable=self.python,
                codex_executable=self.workspace / "missing-codex",
            )

    def test_readiness_is_read_only_and_distinguishes_install_from_runtime(self) -> None:
        def result(code: int):
            return lambda command, **_kwargs: subprocess.CompletedProcess(command, code, "", "")

        self.assertEqual(macos_autostart.readiness(
            self.workspace, launch_agents=self.launch_agents,
            python_executable=self.python, codex_executable=self.python,
            platform="darwin",
        ), {"state": "not_installed"})
        target = self.launch_agents / macos_autostart.PLIST_NAME
        target.write_bytes(macos_autostart.launch_agent(
            self.workspace, python_executable=self.python,
            codex_executable=self.python,
        ))
        os.chmod(target, 0o600)
        self.assertEqual(macos_autostart.readiness(
            self.workspace, launch_agents=self.launch_agents,
            python_executable=self.python, codex_executable=self.python,
            run=result(1), platform="darwin",
        ), {"state": "installed_not_running"})
        self.assertEqual(macos_autostart.readiness(
            self.workspace, launch_agents=self.launch_agents,
            python_executable=self.python, codex_executable=self.python,
            run=result(0), platform="darwin",
        ), {"state": "running"})
        os.chmod(target, 0o644)
        self.assertEqual(macos_autostart.readiness(
            self.workspace, launch_agents=self.launch_agents,
            python_executable=self.python, platform="darwin",
        ), {"state": "needs_attention"})


if __name__ == "__main__":
    unittest.main()
