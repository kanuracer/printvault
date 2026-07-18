"""Production composition helpers for SQL persistence, filesystem roots, and BFF sessions."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api import ApiDependencies, ApiSession
from app.api.sqlalchemy_repository import SQLAlchemyAssetRepository
from app.config import Settings
from app.db import create_engine_from_settings, create_session_factory
from app.migrations import run_migrations
from app.models import Library
from app.services.archive import ArchiveService
from app.services.filesystem import LibraryRootRegistry, RegisteredLibrary, SafeFilesystem
from app.services.indexer import LibraryIndexer
from app.services.thumbnails import ThumbnailCache

_SESSION_COOKIE = "printvault_session"
_SESSION_MAX_AGE_SECONDS = 60 * 60 * 8
_LIBRARY_ROOTS = ("models", "archive")


def configured_library_roots(settings: Settings) -> dict[str, Path]:
    """Return server-configured roots keyed by persisted ``Library.root_name``."""
    return {
        "models": settings.library_models_root,
        "archive": settings.library_archive_root,
    }


def ensure_libraries(session_factory: sessionmaker[Session]) -> None:
    """Idempotently seed the only library identities allowed by the production BFF."""
    with session_factory.begin() as session:
        existing = {library.key: library for library in session.scalars(select(Library)).all()}
        for key in _LIBRARY_ROOTS:
            library = existing.get(key)
            if library is None:
                session.add(Library(key=key, root_name=key))
            elif library.root_name != key:
                raise RuntimeError(f"configured library {key!r} has an unexpected root identity")


def signed_session_resolver(settings: Settings) -> Callable[[Request], ApiSession | None]:
    """Decode the same timed signed session format emitted by ``main._serializer``."""
    secret = settings.session_secret.get_secret_value() if settings.session_secret is not None else ""
    serializer = URLSafeTimedSerializer(secret, salt="printvault.session")

    def resolve(request: Request) -> ApiSession | None:
        cookie = request.cookies.get(_SESSION_COOKIE)
        if not cookie:
            return None
        try:
            payload: Any = serializer.loads(cookie, max_age=_SESSION_MAX_AGE_SECONDS)
        except (BadSignature, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        subject = payload.get("subject")
        role = payload.get("role")
        if not isinstance(subject, str) or not subject or (role is not None and not isinstance(role, str)):
            return None
        return ApiSession(subject=subject, role=role)

    return resolve


def initialize_production_database(settings: Settings) -> None:
    """Migrate, seed exact libraries, then index their configured safe roots."""
    run_migrations(settings.database_url)
    engine = create_engine_from_settings(settings)
    try:
        session_factory = create_session_factory(engine)
        ensure_libraries(session_factory)
        roots = configured_library_roots(settings)
        registry = LibraryRootRegistry(roots)
        with session_factory() as session:
            libraries = session.scalars(select(Library).where(Library.key.in_(_LIBRARY_ROOTS))).all()
            for library in libraries:
                registry.register_library(library)
        repository = SQLAlchemyAssetRepository(
            session_factory,
            SafeFilesystem(registry),
            ArchiveService(registry, registry.library_for_key("archive")),
            ThumbnailCache(settings.thumbnails_root),
        )
        indexer = LibraryIndexer(SafeFilesystem(registry), repository, ThumbnailCache(settings.thumbnails_root))
        with session_factory() as session:
            libraries = session.scalars(select(Library).where(Library.key.in_(_LIBRARY_ROOTS))).all()
            for library in libraries:
                indexer.scan(library)
    finally:
        engine.dispose()


def build_production_dependencies(settings: Settings) -> ApiDependencies:
    """Build real API adapters without opening the database before the lifespan starts."""
    for root in configured_library_roots(settings).values():
        root.mkdir(parents=True, exist_ok=True)
    engine = create_engine_from_settings(settings)
    session_factory = create_session_factory(engine)

    registry = LibraryRootRegistry(configured_library_roots(settings))
    for key in _LIBRARY_ROOTS:
        registry.register_library(RegisteredLibrary(key=key, root_name=key))
    archive_service = ArchiveService(registry, RegisteredLibrary(key="archive", root_name="archive"))
    repository = SQLAlchemyAssetRepository(
        session_factory,
        SafeFilesystem(registry),
        archive_service,
        ThumbnailCache(settings.thumbnails_root),
    )
    return ApiDependencies(repository=repository, session_resolver=signed_session_resolver(settings))


__all__ = [
    "SQLAlchemyAssetRepository",
    "build_production_dependencies",
    "configured_library_roots",
    "ensure_libraries",
    "initialize_production_database",
    "signed_session_resolver",
]
