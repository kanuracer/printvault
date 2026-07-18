from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.db import create_engine_from_settings, create_session_factory
from app.main import _serializer, create_app
from app.models import Asset, AuditEvent, Library


def production_settings(tmp_path: Path) -> Settings:
    roots = {
        "models": tmp_path / "models",
        "projects": tmp_path / "projects",
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
        library_projects_root=roots["projects"],
        library_archive_root=roots["archive"],
        data_root=roots["data"],
        thumbnails_root=roots["thumbnails"],
        session_secret="test-session-secret",
    )


def session_factory(settings: Settings):
    return create_session_factory(create_engine_from_settings(settings))


def signed_session(settings: Settings, *, subject: str, role: str | None) -> str:
    return _serializer(settings, "session").dumps({"subject": subject, "role": role})


def add_asset(settings: Settings, *, library_key: str, relative_path: str, content: bytes = b"solid printvault") -> Asset:
    factory = session_factory(settings)
    with factory.begin() as session:
        library = session.scalar(select(Library).where(Library.key == library_key))
        assert library is not None
        path = {
            "models": settings.library_models_root,
            "projects": settings.library_projects_root,
            "archive": settings.library_archive_root,
        }[library_key] / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        asset = Asset(
            library=library,
            relative_path=relative_path,
            format="stl",
            byte_size=len(content),
            sha256="a" * 64,
        )
        session.add(asset)
        session.flush()
        asset_id = asset.id
    with factory() as session:
        persisted = session.get(Asset, asset_id)
        assert persisted is not None
        return persisted


def test_production_startup_migrates_and_seeds_exact_registered_libraries(tmp_path: Path) -> None:
    settings = production_settings(tmp_path)

    with TestClient(create_app(settings), base_url="https://printvault.example.test"):
        with session_factory(settings)() as session:
            libraries = session.scalars(select(Library).order_by(Library.key)).all()

    assert [(library.key, library.root_name) for library in libraries] == [
        ("archive", "archive"),
        ("models", "models"),
        ("projects", "projects"),
    ]


def test_production_create_app_registers_real_api_routes_before_the_server_starts(tmp_path: Path) -> None:
    app = create_app(production_settings(tmp_path))

    assert "/api/assets" in {route.path for route in app.routes}


def test_startup_indexes_supported_files_from_configured_library_roots(tmp_path: Path) -> None:
    settings = production_settings(tmp_path)
    model = settings.library_models_root / "parts" / "bracket.stl"
    model.parent.mkdir()
    model.write_text("solid bracket\nfacet normal 0 0 0\nendfacet\nendsolid bracket\n", encoding="ascii")

    with TestClient(create_app(settings), base_url="https://printvault.example.test") as client:
        client.cookies.set("printvault_session", signed_session(settings, subject="viewer-1", role="viewer"))
        response = client.get("/api/assets")

    assert response.status_code == 200
    assert [(item["relative_path"], item["format"]) for item in response.json()["items"]] == [("parts/bracket.stl", "stl")]


def test_indexed_asset_exposes_a_private_persisted_sha_thumbnail(tmp_path: Path) -> None:
    settings = production_settings(tmp_path)
    model = settings.library_models_root / "parts" / "bracket.stl"
    model.parent.mkdir()
    model.write_text("solid bracket\nendsolid bracket\n", encoding="ascii")

    with TestClient(create_app(settings), base_url="https://printvault.example.test") as client:
        client.cookies.set("printvault_session", signed_session(settings, subject="viewer-1", role="viewer"))
        asset = client.get("/api/assets").json()["items"][0]
        thumbnail = client.get(f"/api/assets/{asset['id']}/thumbnail")

    assert thumbnail.status_code == 200
    assert thumbnail.headers["content-type"].startswith("image/svg+xml")
    assert thumbnail.headers["cache-control"] == "private, no-cache"
    assert list(settings.thumbnails_root.glob("*/*.svg"))


def test_production_api_resolves_signed_bff_session_and_streams_only_persisted_safe_asset(tmp_path: Path) -> None:
    settings = production_settings(tmp_path)
    with TestClient(create_app(settings), base_url="https://printvault.example.test") as client:
        asset = add_asset(settings, library_key="models", relative_path="parts/bracket.stl", content=b"mesh bytes")
        client.cookies.set("printvault_session", signed_session(settings, subject="viewer-1", role="viewer"))

        response = client.get(f"/api/assets/{asset.id}/download")
        client.cookies.set("printvault_session", "tampered-session")
        rejected = client.get("/api/assets")

    assert response.status_code == 200
    assert response.content == b"mesh bytes"
    assert response.headers["content-type"].startswith("model/stl")
    assert "/tmp/" not in response.text
    assert rejected.status_code == 401


def test_archive_library_assets_are_hidden_from_normal_browse_until_explicitly_requested(tmp_path: Path) -> None:
    settings = production_settings(tmp_path)
    with TestClient(create_app(settings), base_url="https://printvault.example.test") as client:
        model = add_asset(settings, library_key="models", relative_path="visible.stl")
        archived = add_asset(settings, library_key="archive", relative_path="models/hidden.stl")
        client.cookies.set("printvault_session", signed_session(settings, subject="viewer-1", role="viewer"))

        normal = client.get("/api/assets")
        archive = client.get("/api/assets", params={"library": "archive"})

    assert [item["id"] for item in normal.json()["items"]] == [str(model.id)]
    assert [item["id"] for item in archive.json()["items"]] == [str(archived.id)]


def test_production_uploads_multiple_supported_files_to_a_configured_library_and_indexes_them(tmp_path: Path) -> None:
    settings = production_settings(tmp_path)
    with TestClient(create_app(settings), base_url="https://printvault.example.test") as client:
        client.cookies.set("printvault_session", signed_session(settings, subject="editor-1", role="editor"))
        response = client.post(
            "/api/uploads",
            data={"library_key": "models"},
            files=[
                ("files", ("bracket.stl", b"solid bracket\nendsolid bracket\n", "model/stl")),
                ("files", ("case.obj", b"o case\nv 0 0 0\n", "model/obj")),
            ],
        )
        listed = client.get("/api/assets", params={"library": "models"})
        with session_factory(settings)() as session:
            events = session.scalars(select(AuditEvent).order_by(AuditEvent.id)).all()

    assert response.status_code == 200
    assert [(item["relative_path"], item["format"]) for item in response.json()["items"]] == [
        ("bracket.stl", "stl"),
        ("case.obj", "obj"),
    ]
    assert response.json()["rejected"] == []
    assert (settings.library_models_root / "bracket.stl").read_bytes() == b"solid bracket\nendsolid bracket\n"
    assert (settings.library_models_root / "case.obj").read_bytes() == b"o case\nv 0 0 0\n"
    assert {item["relative_path"] for item in listed.json()["items"]} == {"bracket.stl", "case.obj"}
    assert [event.action for event in events] == ["upload", "upload"]


def test_editor_creates_a_logical_project_and_assigns_an_existing_model(tmp_path: Path) -> None:
    settings = production_settings(tmp_path)
    with TestClient(create_app(settings), base_url="https://printvault.example.test") as client:
        asset = add_asset(settings, library_key="models", relative_path="parts/bracket.stl")
        client.cookies.set("printvault_session", signed_session(settings, subject="editor-1", role="editor"))
        created = client.post("/api/projects", json={"name": "Werkbank", "description": "Ersatzteile"})
        project_id = created.json()["id"]
        assigned = client.put(f"/api/projects/{project_id}/assets/{asset.id}")
        projects = client.get("/api/projects")

    assert created.status_code == 201
    assert created.json()["name"] == "Werkbank"
    assert assigned.status_code == 200
    assert projects.json()["items"] == [{
        "id": project_id,
        "name": "Werkbank",
        "description": "Ersatzteile",
        "asset_ids": [str(asset.id)],
    }]


def test_upload_rejects_viewers_before_writing_files(tmp_path: Path) -> None:
    settings = production_settings(tmp_path)
    with TestClient(create_app(settings), base_url="https://printvault.example.test") as client:
        client.cookies.set("printvault_session", signed_session(settings, subject="viewer-1", role="viewer"))
        response = client.post(
            "/api/uploads",
            data={"library_key": "models"},
            files=[("files", ("forbidden.stl", b"solid forbidden", "model/stl"))],
        )

    assert response.status_code == 403
    assert not (settings.library_models_root / "forbidden.stl").exists()


def test_upload_rejects_unsupported_files_without_writing_them(tmp_path: Path) -> None:
    settings = production_settings(tmp_path)
    with TestClient(create_app(settings), base_url="https://printvault.example.test") as client:
        client.cookies.set("printvault_session", signed_session(settings, subject="editor-1", role="editor"))
        response = client.post(
            "/api/uploads",
            data={"library_key": "models"},
            files=[("files", ("not-a-model.txt", b"not a model", "text/plain"))],
        )

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["rejected"] == [{"filename": "not-a-model.txt", "reason": "unsupported_format"}]
    assert not (settings.library_models_root / "not-a-model.txt").exists()


def test_production_archive_restore_and_permanent_delete_update_database_after_filesystem_success(tmp_path: Path) -> None:
    settings = production_settings(tmp_path)
    with TestClient(create_app(settings), base_url="https://printvault.example.test") as client:
        asset = add_asset(settings, library_key="models", relative_path="parts/bracket.stl")
        source = settings.library_models_root / "parts" / "bracket.stl"
        archived = settings.library_archive_root / "models" / "parts" / "bracket.stl"
        client.cookies.set("printvault_session", signed_session(settings, subject="editor-1", role="editor"))

        archive_response = client.post(f"/api/assets/{asset.id}/archive")
        assert archive_response.status_code == 200
        assert not source.exists()
        assert archived.read_bytes() == b"solid printvault"

        restore_response = client.post(f"/api/assets/{asset.id}/restore")
        assert restore_response.status_code == 200
        assert source.read_bytes() == b"solid printvault"
        client.cookies.set("printvault_session", signed_session(settings, subject="admin-1", role="admin"))
        delete_response = client.delete(f"/api/assets/{asset.id}")

        with session_factory(settings)() as session:
            assert session.get(Asset, asset.id) is None
            events = session.scalars(select(AuditEvent).order_by(AuditEvent.id)).all()

    assert delete_response.status_code == 200
    assert not source.exists()
    assert [event.action for event in events] == ["archive", "restore", "permanent_delete"]
    assert events[0].metadata_json["metadata"] == {
        "source_library_key": "models",
        "source_relative_path": "parts/bracket.stl",
        "archive_relative_path": "models/parts/bracket.stl",
    }
    assert events[-1].metadata_json["asset_id"] == str(asset.id)
