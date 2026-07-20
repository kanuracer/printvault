from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from helper.printvault_helper.config import ConfigError, load_config
from helper.printvault_helper.http_client import HttpClient, RequestContext
from helper.printvault_helper.launcher import ConfirmDeclined, LaunchError, launch_from_target


@dataclass
class CapturedProcess:
    args: list[str]
    shell: bool
    close_fds: bool


class FakeHttpClient(HttpClient):
    def __init__(self, job: dict[str, object], content: bytes) -> None:
        self.job = job
        self.content = content
        self.redeem_calls: list[tuple[RequestContext, str]] = []
        self.download_calls: list[tuple[RequestContext, str, Path]] = []

    def redeem_job(self, ctx: RequestContext, request_id: str) -> dict[str, object]:
        self.redeem_calls.append((ctx, request_id))
        return dict(self.job)

    def download_asset(self, ctx: RequestContext, asset_url: str, destination: Path) -> None:
        self.download_calls.append((ctx, asset_url, destination))
        destination.write_bytes(self.content)


class HelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.config_path = self.root / "config.json"
        self.exe_path = self.root / "slicer"
        self.exe_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        os.chmod(self.exe_path, 0o755)
        os.environ["PRINTVAULT_HELPER_TOKEN"] = "secret-token"
        self.now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        os.environ.pop("PRINTVAULT_HELPER_TOKEN", None)
        self.tempdir.cleanup()

    def test_rejects_non_https_origin(self) -> None:
        self._write_config(origin="http://printvault.example.com")
        with self.assertRaises(ConfigError):
            load_config(self.config_path)

    def test_rejects_hash_mismatch(self) -> None:
        config = self._load_default_config()
        client = FakeHttpClient(
            job=self._job(asset_sha256="0" * 64),
            content=b"actual file contents",
        )
        with self.assertRaisesRegex(LaunchError, "SHA-256 mismatch"):
            launch_from_target(
                config=config,
                target="request-1:orca",
                http_client=client,
                confirm=lambda _: "LAUNCH",
                now=lambda: self.now,
                cache_root=self.root / "cache",
            )

    def test_rejects_expired_job(self) -> None:
        config = self._load_default_config()
        client = FakeHttpClient(
            job=self._job(expires_at=(self.now - timedelta(seconds=1)).isoformat()),
            content=b"x",
        )
        with self.assertRaisesRegex(LaunchError, "expired"):
            launch_from_target(
                config=config,
                target="request-1:orca",
                http_client=client,
                confirm=lambda _: "LAUNCH",
                now=lambda: self.now,
                cache_root=self.root / "cache",
            )

    def test_rejects_foreign_asset_url(self) -> None:
        config = self._load_default_config()
        client = FakeHttpClient(
            job=self._job(asset_url="https://evil.example.net/downloads/file.3mf"),
            content=b"x",
        )
        with self.assertRaisesRegex(LaunchError, "must match configured origin"):
            launch_from_target(
                config=config,
                target="request-1:orca",
                http_client=client,
                confirm=lambda _: "LAUNCH",
                now=lambda: self.now,
                cache_root=self.root / "cache",
            )

    def test_requires_local_confirmation(self) -> None:
        config = self._load_default_config()
        payload = b"asset bytes"
        client = FakeHttpClient(job=self._job(asset_sha256=self._sha256(payload)), content=payload)
        with self.assertRaises(ConfirmDeclined):
            launch_from_target(
                config=config,
                target="request-1:orca",
                http_client=client,
                confirm=lambda _: "no",
                now=lambda: self.now,
                cache_root=self.root / "cache",
            )

    def test_constructs_safe_subprocess_args(self) -> None:
        self._write_config(args=["--load", "{file}", "--flag=literal"])
        config = load_config(self.config_path)
        payload = b"asset bytes"
        client = FakeHttpClient(
            job=self._job(asset_name="weird;name $(touch hacked).3mf", asset_sha256=self._sha256(payload)),
            content=payload,
        )
        captured: list[CapturedProcess] = []

        def runner(args: list[str], shell: bool, close_fds: bool) -> CapturedProcess:
            process = CapturedProcess(args=args, shell=shell, close_fds=close_fds)
            captured.append(process)
            return process

        process = launch_from_target(
            config=config,
            target="request-1:orca",
            http_client=client,
            confirm=lambda _: "LAUNCH",
            now=lambda: self.now,
            runner=runner,
            cache_root=self.root / "cache",
        )

        self.assertIs(process, captured[0])
        self.assertFalse(captured[0].shell)
        self.assertTrue(captured[0].close_fds)
        self.assertEqual(captured[0].args[0], str(self.exe_path))
        self.assertEqual(captured[0].args[1], "--load")
        self.assertTrue(captured[0].args[2].endswith("weird;name $(touch hacked).3mf"))
        self.assertEqual(captured[0].args[3], "--flag=literal")
        self.assertEqual(len(captured[0].args), 4)

    def _load_default_config(self):
        self._write_config()
        return load_config(self.config_path)

    def _write_config(self, origin: str = "https://printvault.example.com", args: list[str] | None = None) -> None:
        templates = args or ["{file}"]
        self.config_path.write_text(
            (
                "{\n"
                '  "version": 1,\n'
                f'  "origin": "{origin}",\n'
                '  "user_id": "user-123",\n'
                '  "device_id": "device-456",\n'
                '  "auth": {"type": "bearer_env", "token_env": "PRINTVAULT_HELPER_TOKEN"},\n'
                '  "profiles": [\n'
                "    {\n"
                '      "id": "orca",\n'
                '      "label": "OrcaSlicer",\n'
                f'      "executable": "{self.exe_path}",\n'
                f'      "args": {templates!r}\n'
                "    }\n"
                "  ]\n"
                "}\n"
            ).replace("'", '"'),
            encoding="utf-8",
        )

    def _job(
        self,
        *,
        asset_url: str = "https://printvault.example.com/api/assets/asset-1/download",
        asset_name: str = "part.3mf",
        asset_sha256: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, object]:
        payload = b"default"
        return {
            "request_id": "request-1",
            "profile_id": "orca",
            "user_id": "user-123",
            "device_id": "device-456",
            "asset_url": asset_url,
            "asset_name": asset_name,
            "asset_sha256": asset_sha256 or self._sha256(payload),
            "expires_at": expires_at or (self.now + timedelta(minutes=2)).isoformat(),
        }

    @staticmethod
    def _sha256(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()


if __name__ == "__main__":
    unittest.main()
