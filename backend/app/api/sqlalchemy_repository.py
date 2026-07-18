"""Production SQLAlchemy adapter for the dependency-injected asset API.

This adapter persists only library identities and normalized relative paths.  A
configured ``SafeFilesystem`` is the sole authority that resolves those records
to host files immediately before an I/O operation.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, joinedload, selectinload, sessionmaker

from app.api import AssetQuery, AssetRecord, AuditRecord, DownloadHandle, LibraryRecord, TagRecord
from app.models import Asset, AuditEvent, Library, Tag
from app.services.archive import ArchiveMetadata, ArchiveService
from app.services.filesystem import FileActionResult, PathCollisionError, SafeFilesystem, UnsafePathError
from app.services.indexer import IndexedAsset
from app.services.metadata import fingerprint_model, model_format

_ARCHIVE_LIBRARY_KEY = "archive"
_CONTENT_TYPES = {
    "3mf": "model/3mf",
    "obj": "model/obj",
    "stl": "model/stl",
}


class SQLAlchemyAssetRepository:
    """ORM-backed API repository with safe file operations and immutable audit rows."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        filesystem: SafeFilesystem,
        archive_service: ArchiveService,
    ) -> None:
        self._session_factory = session_factory
        self._filesystem = filesystem
        self._archive_service = archive_service

    def list_libraries(self) -> list[LibraryRecord]:
        with self._session_factory() as session:
            libraries = session.scalars(select(Library).order_by(Library.key)).all()
            return [LibraryRecord(key=library.key, name=library.key.replace("_", " ").title()) for library in libraries]

    def list_assets(self, query: AssetQuery) -> list[AssetRecord]:
        with self._session_factory() as session:
            statement: Select[tuple[Asset]] = select(Asset).join(Asset.library).options(
                joinedload(Asset.library), selectinload(Asset.tags)
            )
            if query.library is None:
                statement = statement.where(Library.key != _ARCHIVE_LIBRARY_KEY)
            else:
                statement = statement.where(Library.key == query.library)
            if query.q:
                statement = statement.where(Asset.relative_path.ilike(f"%{query.q}%"))
            if query.favorite is not None:
                statement = statement.where(Asset.favorite.is_(query.favorite))
            if query.tag:
                statement = statement.where(Asset.tags.any(Tag.key == query.tag))
            if query.format:
                statement = statement.where(Asset.format == query.format.casefold())
            assets = session.scalars(statement.order_by(Asset.id)).all()
            return [self._asset_record(asset) for asset in assets]

    def get_asset(self, asset_id: str) -> AssetRecord | None:
        with self._session_factory() as session:
            asset = self._get_asset(session, asset_id)
            return self._asset_record(asset) if asset is not None else None

    def list_tags(self) -> list[TagRecord]:
        with self._session_factory() as session:
            tags = session.scalars(select(Tag).order_by(Tag.key)).all()
            return [TagRecord(key=tag.key, name=tag.name) for tag in tags]

    def set_tags(self, asset_id: str, tag_keys: set[str], *, actor_subject: str) -> AssetRecord | None:
        with self._session_factory.begin() as session:
            asset = self._get_asset(session, asset_id)
            if asset is None:
                return None
            tags = session.scalars(select(Tag).where(Tag.key.in_(tag_keys))).all()
            if len(tags) != len(tag_keys):
                return None
            asset.tags = tags
            self._audit(session, actor_subject, "assign_tags", asset, {"tag_keys": sorted(tag_keys)})
            session.flush()
            return self._asset_record(asset)

    def set_favorite(self, asset_id: str, favorite: bool, *, actor_subject: str) -> AssetRecord | None:
        with self._session_factory.begin() as session:
            asset = self._get_asset(session, asset_id)
            if asset is None:
                return None
            asset.favorite = favorite
            self._audit(session, actor_subject, "favorite", asset, {"favorite": favorite})
            session.flush()
            return self._asset_record(asset)

    def archive(self, asset_id: str, *, actor_subject: str) -> AssetRecord | None:
        with self._session_factory.begin() as session:
            asset = self._get_asset(session, asset_id)
            if asset is None:
                return None
            result = self._archive_service.archive_asset(asset)
            archive_library = self._library(session, _ARCHIVE_LIBRARY_KEY)
            if archive_library is None:
                raise RuntimeError("the configured archive library is unavailable")
            asset.library = archive_library
            asset.relative_path = result.destination_relative_path
            self._audit(session, actor_subject, "archive", asset, result.audit_metadata())
            session.flush()
            return self._asset_record(asset)

    def restore(self, asset_id: str, *, actor_subject: str) -> AssetRecord | None:
        with self._session_factory.begin() as session:
            asset = self._get_asset(session, asset_id)
            if asset is None:
                return None
            metadata = self._latest_archive_metadata(session, asset)
            if metadata is None:
                return None
            result = self._archive_service.restore(metadata)
            library = self._library(session, metadata.source_library_key)
            if library is None:
                raise RuntimeError("the recorded source library is unavailable")
            asset.library = library
            asset.relative_path = metadata.source_relative_path
            self._audit(session, actor_subject, "restore", asset, result.audit_metadata())
            session.flush()
            return self._asset_record(asset)

    def move(
        self, asset_id: str, *, destination_library_key: str, destination_relative_path: str, actor_subject: str
    ) -> AssetRecord | None:
        with self._session_factory.begin() as session:
            asset = self._get_asset(session, asset_id)
            destination = self._library(session, destination_library_key)
            if asset is None or destination is None:
                return None
            result = self._filesystem.move_asset(asset, destination, destination_relative_path)
            asset.library = destination
            asset.relative_path = result.destination_relative_path or destination_relative_path
            self._audit(session, actor_subject, "move", asset, result.audit_metadata())
            session.flush()
            return self._asset_record(asset)

    def permanently_delete(self, asset_id: str, *, actor_subject: str) -> bool:
        with self._session_factory.begin() as session:
            asset = self._get_asset(session, asset_id)
            if asset is None:
                return False
            result = self._archive_service.permanently_delete(asset, authorized=True)
            if not result.performed:
                return False
            # The FK is SET NULL after the asset is removed, so retain its public
            # stable ID in JSON for audit listing without retaining a deleted ORM row.
            metadata = result.audit_metadata() | {"asset_id": str(asset.id)}
            self._audit(session, actor_subject, "permanent_delete", asset, metadata)
            session.delete(asset)
            return True

    def upload(self, library_key: str, filename: str, stream: BinaryIO, *, actor_subject: str) -> AssetRecord:
        if library_key == _ARCHIVE_LIBRARY_KEY:
            raise ValueError("archive uploads are not allowed")
        destination: Path | None = None
        created = False
        try:
            with self._session_factory.begin() as session:
                library = self._library(session, library_key)
                if library is None:
                    raise ValueError("unknown library")
                destination = self._filesystem.prepare_destination(library, filename)
                self._write_upload(stream, destination)
                created = True
                fingerprint = fingerprint_model(destination)
                asset = Asset(
                    library=library,
                    relative_path=filename,
                    format=model_format(destination),
                    byte_size=fingerprint.byte_size,
                    sha256=fingerprint.sha256,
                )
                session.add(asset)
                session.flush()
                self._audit(session, actor_subject, "upload", asset, {"filename": filename, "byte_size": fingerprint.byte_size})
                return self._asset_record(asset)
        except Exception:
            if created and destination is not None:
                destination.unlink(missing_ok=True)
            raise

    @staticmethod
    def _write_upload(stream: BinaryIO, destination: Path, *, max_bytes: int = 512 * 1024 * 1024) -> None:
        stream.seek(0)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(destination, flags, 0o640)
        written = 0
        try:
            with os.fdopen(descriptor, "wb") as output:
                while chunk := stream.read(64 * 1024):
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError("upload exceeds configured limit")
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    def list_audit(self) -> list[AuditRecord]:
        with self._session_factory() as session:
            events = session.scalars(select(AuditEvent).order_by(AuditEvent.id)).all()
            return [
                AuditRecord(
                    actor_subject=event.actor_subject,
                    action=event.action,
                    asset_id=str(event.asset_id) if event.asset_id is not None else self._audit_asset_id(event),
                )
                for event in events
            ]

    def open_download(self, asset_id: str) -> DownloadHandle | None:
        with self._session_factory() as session:
            asset = self._get_asset(session, asset_id)
            if asset is None:
                return None
            try:
                path = self._filesystem.resolve_asset(asset, require_regular=True)
                stream = path.open("rb")
            except (OSError, UnsafePathError):
                return None
            return DownloadHandle(
                filename=Path(asset.relative_path).name,
                content_type=_CONTENT_TYPES.get(asset.format.casefold(), "application/octet-stream"),
                stream=stream,
            )

    # ``IndexRepository`` compatibility lets a real scan hand its safe discovery
    # records directly to production persistence without accepting host paths.
    def get(self, library_key: str, relative_path: str) -> IndexedAsset | None:
        with self._session_factory() as session:
            asset = session.scalar(
                select(Asset).join(Asset.library).where(Library.key == library_key, Asset.relative_path == relative_path)
            )
            if asset is None or asset.sha256 is None:
                return None
            from app.services.metadata import FileFingerprint, GeometryMetadata

            return IndexedAsset(
                library_key=library_key,
                relative_path=asset.relative_path,
                format=asset.format,
                fingerprint=FileFingerprint(asset.sha256, asset.byte_size, 0, asset.format),
                geometry=GeometryMetadata(),
                thumbnail_path="",
            )

    def create(self, indexed: IndexedAsset) -> None:
        self._upsert_indexed(indexed, create_only=True)

    def update(self, indexed: IndexedAsset) -> None:
        self._upsert_indexed(indexed, create_only=False)

    def mark_missing(self, library_key: str, present_relative_paths: set[str]) -> int:
        # The current persisted domain model intentionally has no ``missing``
        # column.  Do not delete records merely because a scan cannot see them.
        return 0

    def _upsert_indexed(self, indexed: IndexedAsset, *, create_only: bool) -> None:
        with self._session_factory.begin() as session:
            library = self._library(session, indexed.library_key)
            if library is None:
                raise ValueError("indexed asset references an unknown library")
            asset = session.scalar(select(Asset).where(Asset.library_id == library.id, Asset.relative_path == indexed.relative_path))
            if asset is None:
                asset = Asset(
                    library=library,
                    relative_path=indexed.relative_path,
                    format=indexed.format,
                    byte_size=indexed.fingerprint.byte_size,
                    sha256=indexed.fingerprint.sha256,
                )
                session.add(asset)
            elif not create_only:
                asset.format = indexed.format
                asset.byte_size = indexed.fingerprint.byte_size
                asset.sha256 = indexed.fingerprint.sha256

    @staticmethod
    def _asset_record(asset: Asset) -> AssetRecord:
        return AssetRecord(
            id=str(asset.id),
            library_key=asset.library.key,
            relative_path=asset.relative_path,
            format=asset.format,
            favorite=asset.favorite,
            tags={tag.key for tag in asset.tags},
            archived=asset.library.key == _ARCHIVE_LIBRARY_KEY,
            byte_size=asset.byte_size,
        )

    @staticmethod
    def _get_asset(session: Session, asset_id: str) -> Asset | None:
        try:
            primary_key = int(asset_id)
        except (TypeError, ValueError):
            return None
        return session.scalar(
            select(Asset).options(joinedload(Asset.library), selectinload(Asset.tags)).where(Asset.id == primary_key)
        )

    @staticmethod
    def _library(session: Session, key: str) -> Library | None:
        return session.scalar(select(Library).where(Library.key == key))

    @staticmethod
    def _audit(session: Session, actor_subject: str, action: str, asset: Asset, metadata: dict[str, object]) -> None:
        session.add(AuditEvent(actor_subject=actor_subject, action=action, asset=asset, metadata_json=metadata))

    @staticmethod
    def _audit_asset_id(event: AuditEvent) -> str | None:
        asset_id = event.metadata_json.get("asset_id") if isinstance(event.metadata_json, dict) else None
        return asset_id if isinstance(asset_id, str) else None

    @staticmethod
    def _latest_archive_metadata(session: Session, asset: Asset) -> ArchiveMetadata | None:
        event = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.asset_id == asset.id, AuditEvent.action == "archive")
            .order_by(AuditEvent.id.desc())
        )
        if event is None or not isinstance(event.metadata_json, dict):
            return None
        raw = event.metadata_json.get("metadata")
        if not isinstance(raw, dict):
            return None
        try:
            return ArchiveMetadata(
                source_library_key=raw["source_library_key"],
                source_relative_path=raw["source_relative_path"],
                archive_relative_path=raw["archive_relative_path"],
            )
        except (KeyError, TypeError):
            return None


__all__ = ["SQLAlchemyAssetRepository"]
