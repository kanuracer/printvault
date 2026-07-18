from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.config import Settings
from app.db import create_engine_from_settings, create_session_factory


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
