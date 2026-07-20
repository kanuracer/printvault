from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select, text

from app.api.sqlalchemy_repository import SQLAlchemyAssetRepository
from app.config import Settings
from app.db import Base, create_engine_from_settings, create_session_factory
from app.models import Asset, AuditEvent, Library, LibraryExcludeRule, Project, Tag
from app.services.archive import ArchiveService
from app.services.filesystem import LibraryRootRegistry, RegisteredLibrary, SafeFilesystem
from app.services.thumbnails import ThumbnailCache


def settings_for_sqlite(tmp_path: Path, database_url: str = "sqlite:///:memory:") -> Settings:
    return Settings(
        _env_file=None,
        environment="development",
        database_url=database_url,
        library_models_root=tmp_path / "models",
        library_archive_root=tmp_path / "archive",
        data_root=tmp_path / "data",
        thumbnails_root=tmp_path / "thumbnails",
    )


def test_sqlite_engine_and_session_factory_execute_queries(tmp_path: Path) -> None:
    engine = create_engine_from_settings(settings_for_sqlite(tmp_path))
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        assert session.execute(text("SELECT 1")).scalar_one() == 1

    engine.dispose()


def test_first_project_assignment_response_contains_the_new_asset_link(tmp_path: Path) -> None:
    engine = create_engine_from_settings(settings_for_sqlite(tmp_path))
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    models_root = tmp_path / "models"
    archive_root = tmp_path / "archive"
    models_root.mkdir()
    archive_root.mkdir()

    with session_factory.begin() as session:
        models = Library(key="models", root_name="models")
        asset = Asset(library=models, relative_path="widget.stl", format="stl", byte_size=12)
        project = Project(name="Werkbank", description="")
        session.add_all((models, Library(key="archive", root_name="archive"), asset, project))
        session.flush()
        asset_id = str(asset.id)
        project_id = str(project.id)

    registry = LibraryRootRegistry({"models": models_root, "archive": archive_root})
    repository = SQLAlchemyAssetRepository(
        session_factory,
        SafeFilesystem(registry),
        ArchiveService(registry, RegisteredLibrary(key="archive", root_name="archive")),
        ThumbnailCache(tmp_path / "thumbnails"),
    )

    assigned = repository.assign_project_asset(project_id, asset_id, actor_subject="editor-1")

    assert assigned is not None
    assert assigned.asset_ids == (asset_id,)
    engine.dispose()


def test_mariadb_compatible_mysql_url_creates_mysql_dialect_engine(tmp_path: Path) -> None:
    settings = settings_for_sqlite(
        tmp_path,
        database_url="mysql+pymysql://printvault:password@db.example.test:3306/printvault",
    )

    engine = create_engine_from_settings(settings)

    assert engine.dialect.name == "mysql"
    engine.dispose()


def test_native_mariadb_url_creates_mariadb_dialect_engine(tmp_path: Path) -> None:
    settings = settings_for_sqlite(
        tmp_path,
        database_url="mariadb+mariadbconnector://printvault:password@db.example.test:3306/printvault",
    )

    engine = create_engine_from_settings(settings)

    assert engine.dialect.name == "mysql"
    assert engine.url.drivername == "mysql+pymysql"
    engine.dispose()


def test_initial_alembic_migration_creates_versioned_application_table(tmp_path: Path) -> None:
    database_path = tmp_path / "printvault.sqlite3"
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

    command.upgrade(config, "head")

    tables = inspect(create_engine_from_settings(settings_for_sqlite(tmp_path, f"sqlite:///{database_path}"))).get_table_names()
    assert "alembic_version" in tables
    assert "application_settings" in tables


def test_library_exclude_rule_updates_preserve_asset_metadata_relationships_and_audit_history(tmp_path: Path) -> None:
    engine = create_engine_from_settings(settings_for_sqlite(tmp_path))
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    models_root = tmp_path / "models"
    archive_root = tmp_path / "archive"
    models_root.mkdir()
    archive_root.mkdir()

    with session_factory.begin() as session:
        models = Library(key="models", root_name="models")
        archive = Library(key="archive", root_name="archive")
        asset = Asset(library=models, relative_path="widget.stl", format="stl", byte_size=12, favorite=True)
        tag = Tag(name="Functional")
        project = Project(name="Werkbank", description="")
        asset.tags = [tag]
        project.assets = [asset]
        session.add_all((models, archive, asset, tag, project))
        session.add(AuditEvent(actor_subject="seed", action="favorite", asset=asset, metadata_json={"favorite": True}))
        session.flush()
        asset_id = asset.id
        project_id = project.id

    registry = LibraryRootRegistry({"models": models_root, "archive": archive_root})
    repository = SQLAlchemyAssetRepository(
        session_factory,
        SafeFilesystem(registry),
        ArchiveService(registry, RegisteredLibrary(key="archive", root_name="archive")),
        ThumbnailCache(tmp_path / "thumbnails"),
    )

    added = repository.add_library_exclude_rule("models", "drafts/**/*.stl", actor_subject="admin-1")
    removed = repository.remove_library_exclude_rule("models", "drafts/**/*.stl", actor_subject="admin-1")

    assert added is not None
    assert [item.pattern for item in added] == ["drafts/**/*.stl"]
    assert removed == []

    with session_factory() as session:
        persisted_asset = session.get(Asset, asset_id)
        persisted_project = session.get(Project, project_id)
        rules = session.scalars(select(LibraryExcludeRule)).all()
        events = session.scalars(select(AuditEvent).order_by(AuditEvent.id)).all()

        assert persisted_asset is not None
        assert persisted_asset.favorite is True
        assert [tag.key for tag in persisted_asset.tags] == ["functional"]
        assert persisted_project is not None
        assert {linked.id for linked in persisted_project.assets} == {asset_id}
        assert rules == []
        assert [event.action for event in events] == ["favorite", "add_library_exclude_rule", "remove_library_exclude_rule"]
    engine.dispose()
