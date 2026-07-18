from pathlib import Path

from sqlalchemy import create_engine, inspect


def test_run_migrations_creates_initial_schema(tmp_path: Path) -> None:
    from app.migrations import run_migrations

    database_path = tmp_path / "migration.sqlite3"
    run_migrations(f"sqlite:///{database_path}")

    assert {"alembic_version", "application_settings"} <= set(inspect(create_engine(f"sqlite:///{database_path}")).get_table_names())
