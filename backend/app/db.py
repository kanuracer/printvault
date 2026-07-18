"""SQLAlchemy database engine and session factory helpers."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings


class Base(DeclarativeBase):
    """Declarative metadata shared by PrintVault domain models."""


def create_engine_from_settings(settings: Settings) -> Engine:
    """Create a SQLAlchemy engine for either SQLite or MariaDB/MySQL."""
    database_url = make_url(settings.database_url)
    # Use the pure-Python PyMySQL driver for native MariaDB URL syntax so the
    # container does not need MariaDB Connector/C build dependencies.
    if database_url.get_backend_name() == "mariadb":
        database_url = database_url.set(drivername="mysql+pymysql")

    engine_options: dict[str, object] = {"future": True, "pool_pre_ping": True}
    if database_url.get_backend_name() == "sqlite":
        engine_options["connect_args"] = {"check_same_thread": False}
    return create_engine(database_url, **engine_options)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return the application's configured SQLAlchemy session factory."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    """Yield a session suitable for a FastAPI dependency."""
    with session_factory() as session:
        yield session
