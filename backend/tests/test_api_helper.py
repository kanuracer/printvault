from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import ApiDependencies, ApiSession, register_api
from app.api.sqlalchemy_repository import SQLAlchemyAssetRepository
from app.db import create_engine_from_settings, create_session_factory
from app.config import Settings
from app.migrations import run_migrations
from app.models import Asset, AuditEvent, Library
from app.services.archive import ArchiveService
from app.services.filesystem import LibraryRootRegistry, RegisteredLibrary, SafeFilesystem
from app.services.thumbnails import ThumbnailCache


@dataclass
class MutableClock:
    current: datetime

    def now(self) -> datetime:
        return self.current


class CookieSessions:
    def __init__(self) -> None:
        self._sessions = {
            "viewer-cookie": ApiSession(subject="viewer-subject", role="viewer"),
            "other-cookie": ApiSession(subject="other-subject", role="viewer"),
            "admin-cookie": ApiSession(subject="admin-subject", role="admin"),
        }

    def resolve(self, request: Request) -> ApiSession | None:
        return self._sessions.get(request.cookies.get("printvault_session", ""))


def helper_settings(tmp_path: Path) -> Settings:
    roots = {
        "models": tmp_path / "models",
        "archive": tmp_path / "archive",
        "data": tmp_path / "data",
        "thumbnails": tmp_path / "thumbnails",
    }
    for root in roots.values():
        root.mkdir()
    return Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'printvault.sqlite3'}",
        library_models_root=roots["models"],
        library_archive_root=roots["archive"],
        data_root=roots["data"],
        thumbnails_root=roots["thumbnails"],
        session_secret="test-session-secret",
    )


def make_app(tmp_path: Path, clock: MutableClock) -> tuple[FastAPI, SQLAlchemyAssetRepository, Settings]:
    settings = helper_settings(tmp_path)
    run_migrations(settings.database_url)
    engine = create_engine_from_settings(settings)
    session_factory = create_session_factory(engine)
    registry = LibraryRootRegistry({"models": settings.library_models_root, "archive": settings.library_archive_root})
    repository = SQLAlchemyAssetRepository(
        session_factory,
        SafeFilesystem(registry),
        ArchiveService(registry, RegisteredLibrary(key="archive", root_name="archive")),
        ThumbnailCache(settings.thumbnails_root),
        helper_secret=settings.session_secret.get_secret_value() if settings.session_secret is not None else "",
    )
    with session_factory.begin() as session:
        session.add_all((Library(key="models", root_name="models"), Library(key="archive", root_name="archive")))
    app = FastAPI()
    register_api(app, ApiDependencies(repository=repository, session_resolver=CookieSessions().resolve, now=clock.now))
    return app, repository, settings


def add_asset(settings: Settings, *, relative_path: str, content: bytes) -> str:
    session_factory = create_session_factory(create_engine_from_settings(settings))
    sha256 = hashlib.sha256(content).hexdigest()
    with session_factory.begin() as session:
        library = session.scalar(select(Library).where(Library.key == "models"))
        assert library is not None
        path = settings.library_models_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        asset = Asset(
            library=library,
            relative_path=relative_path,
            format=relative_path.rsplit(".", 1)[-1].casefold(),
            byte_size=len(content),
            sha256=sha256,
        )
        session.add(asset)
        session.flush()
        return str(asset.id)


def test_helper_device_pairing_job_redeem_and_download_flow(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 7, 20, 12, 0, tzinfo=UTC))
    app, _, settings = make_app(tmp_path, clock)
    asset_id = add_asset(settings, relative_path="parts/bracket.stl", content=b"mesh-bytes")
    with TestClient(app, base_url="https://printvault.example.test") as client:
        client.cookies.set("printvault_session", "viewer-cookie")
        pairing = client.post("/api/helper/pairing-codes")
        assert pairing.status_code == 201
        pairing_code = pairing.json()["pairing_code"]

        registered = client.post(
            "/api/helper/devices/register",
            json={"pairing_code": pairing_code, "device_name": "Alice Laptop"},
        )
        assert registered.status_code == 201
        body = registered.json()
        assert body["user_id"] == "viewer-subject"
        assert body["device_id"].startswith("device_")
        assert body["device_credential"]

        job = client.post(
            "/api/helper/jobs",
            json={
                "device_id": body["device_id"],
                "asset_id": asset_id,
                "profile_id": "orca",
                "expires_in_seconds": 120,
            },
        )
        assert job.status_code == 201
        job_body = job.json()
        assert job_body["launch_uri"] == f"printvault://open?request={job_body['request_id']}&profile=orca"

        redeemed = client.post(
            "/api/helper/jobs/redeem",
            json={"request_id": job_body["request_id"], "device_id": body["device_id"], "user_id": "viewer-subject"},
            headers={"Authorization": f"Bearer {body['device_credential']}"},
        )
        assert redeemed.status_code == 200
        redeem_body = redeemed.json()
        assert redeem_body["profile_id"] == "orca"
        assert redeem_body["device_id"] == body["device_id"]
        assert redeem_body["user_id"] == "viewer-subject"
        assert redeem_body["asset_url"] == f"https://printvault.example.test/api/helper/jobs/{job_body['request_id']}/asset"
        assert redeem_body["asset_sha256"] == hashlib.sha256(b"mesh-bytes").hexdigest()
        assert "/root/" not in redeemed.text

        download = client.get(
            f"/api/helper/jobs/{job_body['request_id']}/asset",
            headers={"Authorization": f"Bearer {body['device_credential']}"},
        )
        assert download.status_code == 200
        assert download.content == b"mesh-bytes"

        client.cookies.set("printvault_session", "admin-cookie")
        audit = client.get("/api/audit")
        assert audit.status_code == 200
        assert [item["action"] for item in audit.json()["items"][-4:]] == [
            "issue_helper_pairing_code",
            "register_helper_device",
            "create_helper_job",
            "redeem_helper_job",
        ]


def test_helper_job_rejects_cross_user_cross_device_expiry_replay_and_five_minute_cap(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 7, 20, 12, 0, tzinfo=UTC))
    app, _, settings = make_app(tmp_path, clock)
    asset_id = add_asset(settings, relative_path="parts/widget.stl", content=b"widget-bytes")
    with TestClient(app, base_url="https://printvault.example.test") as client:
        client.cookies.set("printvault_session", "viewer-cookie")
        viewer_pair = client.post("/api/helper/pairing-codes").json()["pairing_code"]
        viewer_device = client.post(
            "/api/helper/devices/register",
            json={"pairing_code": viewer_pair, "device_name": "Viewer Device"},
        ).json()

        client.cookies.set("printvault_session", "other-cookie")
        other_pair = client.post("/api/helper/pairing-codes").json()["pairing_code"]
        other_device = client.post(
            "/api/helper/devices/register",
            json={"pairing_code": other_pair, "device_name": "Other Device"},
        ).json()

        too_long = client.post(
            "/api/helper/jobs",
            json={
                "device_id": other_device["device_id"],
                "asset_id": asset_id,
                "profile_id": "orca",
                "expires_in_seconds": 301,
            },
        )
        assert too_long.status_code == 422

        client.cookies.set("printvault_session", "viewer-cookie")
        foreign_device = client.post(
            "/api/helper/jobs",
            json={
                "device_id": other_device["device_id"],
                "asset_id": asset_id,
                "profile_id": "orca",
                "expires_in_seconds": 120,
            },
        )
        assert foreign_device.status_code == 404

        expired = client.post(
            "/api/helper/jobs",
            json={
                "device_id": viewer_device["device_id"],
                "asset_id": asset_id,
                "profile_id": "orca",
                "expires_in_seconds": 60,
            },
        )
        assert expired.status_code == 201
        clock.current += timedelta(seconds=61)
        expired_redeem = client.post(
            "/api/helper/jobs/redeem",
            json={
                "request_id": expired.json()["request_id"],
                "device_id": viewer_device["device_id"],
                "user_id": "viewer-subject",
            },
            headers={"Authorization": f"Bearer {viewer_device['device_credential']}"},
        )
        assert expired_redeem.status_code == 403

        clock.current = datetime(2026, 7, 20, 12, 5, tzinfo=UTC)
        active = client.post(
            "/api/helper/jobs",
            json={
                "device_id": viewer_device["device_id"],
                "asset_id": asset_id,
                "profile_id": "orca",
                "expires_in_seconds": 60,
            },
        )
        request_id = active.json()["request_id"]

        wrong_device = client.post(
            "/api/helper/jobs/redeem",
            json={"request_id": request_id, "device_id": other_device["device_id"], "user_id": "other-subject"},
            headers={"Authorization": f"Bearer {other_device['device_credential']}"},
        )
        assert wrong_device.status_code == 403

        correct_redeem = client.post(
            "/api/helper/jobs/redeem",
            json={"request_id": request_id, "device_id": viewer_device["device_id"], "user_id": "viewer-subject"},
            headers={"Authorization": f"Bearer {viewer_device['device_credential']}"},
        )
        assert correct_redeem.status_code == 200

        replay = client.post(
            "/api/helper/jobs/redeem",
            json={"request_id": request_id, "device_id": viewer_device["device_id"], "user_id": "viewer-subject"},
            headers={"Authorization": f"Bearer {viewer_device['device_credential']}"},
        )
        assert replay.status_code == 403


def test_helper_devices_are_listed_per_owner_without_credentials_and_can_be_revoked(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 7, 20, 12, 0, tzinfo=UTC))
    app, repository, _ = make_app(tmp_path, clock)
    with TestClient(app) as client:
        client.cookies.set("printvault_session", "viewer-cookie")
        viewer_pairing = client.post("/api/helper/pairing-codes")
        viewer_device = client.post(
            "/api/helper/devices/register",
            json={"pairing_code": viewer_pairing.json()["pairing_code"], "device_name": "Viewer Laptop"},
        ).json()

        client.cookies.set("printvault_session", "other-cookie")
        other_pairing = client.post("/api/helper/pairing-codes")
        other_device = client.post(
            "/api/helper/devices/register",
            json={"pairing_code": other_pairing.json()["pairing_code"], "device_name": "Other Laptop"},
        ).json()

        listed = client.get("/api/helper/devices")
        assert listed.status_code == 200
        listed_items = listed.json()["items"]
        assert len(listed_items) == 1
        assert listed_items[0]["device_id"] == other_device["device_id"]
        assert listed_items[0]["name"] == "Other Laptop"
        assert listed_items[0]["created_at"]
        assert set(listed_items[0]) == {"device_id", "name", "created_at"}
        assert "device_credential" not in listed.text

        client.cookies.set("printvault_session", "viewer-cookie")
        foreign_revoke = client.delete(f"/api/helper/devices/{other_device['device_id']}")
        assert foreign_revoke.status_code == 404

        own_revoke = client.delete(f"/api/helper/devices/{viewer_device['device_id']}")
        assert own_revoke.status_code == 204
        assert client.get("/api/helper/devices").json() == {"items": []}
        assert repository.authenticate_helper_device(viewer_device["device_credential"]) is None
        assert repository.authenticate_helper_device(other_device["device_credential"]) is not None

        client.cookies.set("printvault_session", "admin-cookie")
        audit = client.get("/api/audit")
        assert audit.status_code == 200
        assert audit.json()["items"][-1]["action"] == "revoke_helper_device"
