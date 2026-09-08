from __future__ import annotations

from http.client import HTTPConnection
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from pipeline import remote_access
from pipeline.review_api import create_server
from pipeline.tests.test_review_api import ReviewAPITests


class RemoteAccessConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        (self.workspace / "runtime").mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, value: dict, *, mode: int = 0o600) -> Path:
        path = self.workspace / "runtime/remote-access.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(path, mode)
        return path

    @staticmethod
    def enabled() -> dict:
        return {
            "version": 1,
            "enabled": True,
            "provider": "tailscale-serve",
            "origin": "https://growth-desk.example.ts.net",
            "allowed_logins": ["operator-login"],
        }

    def test_missing_and_disabled_are_local_only(self) -> None:
        self.assertEqual(remote_access.readiness(self.workspace)["state"], "local_only")
        value = self.enabled()
        value.update(enabled=False, origin=None, allowed_logins=[])
        self.write(value)
        self.assertFalse(remote_access.load(self.workspace)["enabled"])

    def test_enabled_requires_canonical_tailnet_origin_and_login(self) -> None:
        self.write(self.enabled())
        self.assertEqual(remote_access.load(self.workspace)["allowed_logins"], ["operator-login"])
        for origin in ["http://growth-desk.example.ts.net", "https://example.com", "https://growth-desk.example.ts.net/", "https://growth-desk.example.ts.net:443"]:
            with self.subTest(origin=origin):
                value = self.enabled()
                value["origin"] = origin
                self.write(value)
                with self.assertRaises(remote_access.RemoteAccessError):
                    remote_access.load(self.workspace)

    def test_unsafe_file_shape_permissions_and_symlink_fail_closed(self) -> None:
        path = self.write(self.enabled(), mode=0o644)
        with self.assertRaises(remote_access.RemoteAccessError):
            remote_access.load(self.workspace)
        value = self.enabled()
        value["extra"] = True
        self.write(value)
        with self.assertRaises(remote_access.RemoteAccessError):
            remote_access.load(self.workspace)
        path.unlink()
        elsewhere = self.workspace / "elsewhere.json"
        elsewhere.write_text(json.dumps(self.enabled()), encoding="utf-8")
        path.symlink_to(elsewhere)
        with self.assertRaises(remote_access.RemoteAccessError):
            remote_access.load(self.workspace)


class RemoteAccessHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = ReviewAPITests()
        self.fixture.setUp()
        runtime = self.fixture.workspace / "runtime"
        runtime.mkdir(exist_ok=True)
        config = {
            "version": 1,
            "enabled": True,
            "provider": "tailscale-serve",
            "origin": "https://growth-desk.example.ts.net",
            "allowed_logins": ["operator-login"],
        }
        path = runtime / "remote-access.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        os.chmod(path, 0o600)
        self.server = create_server(self.fixture.workspace, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def request(self, headers: dict[str, str]) -> tuple[int, dict]:
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request("GET", "/api/projects", headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_exact_tailscale_identity_and_origin_are_required(self) -> None:
        accepted = {
            "Host": "growth-desk.example.ts.net",
            "Origin": "https://growth-desk.example.ts.net",
            "Tailscale-User-Login": "operator-login",
        }
        self.assertEqual(self.request(accepted)[0], 200)
        for changed in [
            {**accepted, "Tailscale-User-Login": "other-login"},
            {key: value for key, value in accepted.items() if key != "Tailscale-User-Login"},
            {**accepted, "Origin": "https://evil.example"},
            {**accepted, "Host": "growth-desk.example.ts.net:444"},
            {"Host": "rebinding.example"},
        ]:
            with self.subTest(headers=changed):
                self.assertEqual(self.request(changed)[0], 403)

    def test_loopback_access_still_works(self) -> None:
        self.assertEqual(self.request({"Host": f"127.0.0.1:{self.server.server_port}"})[0], 200)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
