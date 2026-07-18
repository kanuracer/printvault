"""Database migration entry point used by container startup."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"


def normalized_database_url(database_url: str) -> str:
    """Use the packaged PyMySQL driver for MariaDB URL aliases."""
    parsed = make_url(database_url)
    if parsed.get_backend_name() == "mariadb":
        return str(parsed.set(drivername="mysql+pymysql"))
    return database_url


def run_migrations(database_url: str) -> None:
    """Upgrade the configured MariaDB or SQLite database to the current head."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", normalized_database_url(database_url))
    command.upgrade(config, "head")
