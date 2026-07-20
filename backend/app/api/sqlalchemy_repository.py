"""Production SQLAlchemy adapter for the dependency-injected asset API.

This adapter persists only library identities and normalized relative paths.  A
configured ``SafeFilesystem`` is the sole authority that resolves those records
to host files immediately before an I/O operation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import stat
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from typing import Any

from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session, joinedload, selectinload, sessionmaker

from app.api import AssetPage, AssetQuery, AssetRecord, AuditRecord, DownloadHandle, HelperDevicePrincipal, HelperDeviceRecord, HelperJobAccess, HelperJobRecord, LibraryExcludeRuleRecord, LibraryRecord, PairingCodeRecord, ProjectFolderRecord, ProjectRecord, TagRecord
from app.models import Asset, AuditEvent, HelperDevice, HelperJob, HelperPairingCode, Library, LibraryExcludeRule, Project, ProjectAsset, ProjectFolder, Tag, UserPreference
from app.services.archive import ArchiveMetadata, ArchiveService
from app.services.filesystem import FileActionResult, PathCollisionError, SafeFilesystem, UnsafePathError
from app.services.indexer import IndexedAsset
from app.services.metadata import fingerprint_model, model_format
from app.services.thumbnails import ThumbnailCache
from app.services.three_mf_metadata import ThreeMfExtractionError, extract_three_mf_metadata

_ARCHIVE_LIBRARY_KEY = "archive"
_CONTENT_TYPES = {
    "3mf": "model/3mf",
    "obj": "model/obj",
    "stl": "model/stl",
}


def _is_expired(expires_at: datetime, now: datetime) -> bool:
    """Compare SQLite's naive timestamps with UTC application timestamps."""
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return expires_at <= now


def _three_mf_metadata(path: Path, format_name: str) -> dict[str, object]:
    if format_name != "3mf":
        return {}
    try:
        extraction = extract_three_mf_metadata(path)
    except (OSError, ValueError, ThreeMfExtractionError):
        return {}
    presentation: dict[str, object] = {
        "core": dict(extraction.metadata),
        "documents": [
            {"label": item.display_label, "content_type": item.content_type, "byte_size": item.byte_size,
             **({"text": item.text_content} if item.text_content is not None else {})}
            for item in extraction.documents
        ],
    }
    if extraction.build_colors:
        presentation["build_colors"] = list(extraction.build_colors)
    if extraction.build_transforms:
        presentation["build_transforms"] = [list(transform) for transform in extraction.build_transforms]
    return {"three_mf": presentation}


class SQLAlchemyAssetRepository:
    """ORM-backed API repository with safe file operations and immutable audit rows."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        filesystem: SafeFilesystem,
        archive_service: ArchiveService,
        thumbnails: ThumbnailCache,
        *,
        helper_secret: str = "",
    ) -> None:
        self._session_factory = session_factory
        self._filesystem = filesystem
        self._archive_service = archive_service
        self._thumbnails = thumbnails
        self._helper_secret = helper_secret.encode("utf-8")

    def list_libraries(self) -> list[LibraryRecord]:
        with self._session_factory() as session:
            libraries = session.scalars(select(Library).where(Library.key.in_(("models", "archive"))).order_by(Library.key)).all()
            return [LibraryRecord(key=library.key, name=library.key.replace("_", " ").title()) for library in libraries]

    def list_library_exclude_rules(self, library_key: str) -> list[LibraryExcludeRuleRecord] | None:
        with self._session_factory() as session:
            library = self._library(session, library_key)
            if library is None:
                return None
            rules = session.scalars(
                select(LibraryExcludeRule)
                .where(LibraryExcludeRule.library_id == library.id)
                .order_by(LibraryExcludeRule.pattern, LibraryExcludeRule.id)
            ).all()
            return [LibraryExcludeRuleRecord(pattern=rule.pattern) for rule in rules]

    def add_library_exclude_rule(
        self, library_key: str, pattern: str, *, actor_subject: str
    ) -> list[LibraryExcludeRuleRecord] | None:
        with self._session_factory.begin() as session:
            library = self._library(session, library_key)
            if library is None:
                return None
            existing = session.scalar(
                select(LibraryExcludeRule).where(
                    LibraryExcludeRule.library_id == library.id, LibraryExcludeRule.pattern == pattern
                )
            )
            if existing is None:
                session.add(LibraryExcludeRule(library=library, pattern=pattern))
                self._audit(
                    session,
                    actor_subject,
                    "add_library_exclude_rule",
                    None,
                    {"library_key": library.key, "pattern": pattern},
                )
            session.flush()
            return self._list_library_exclude_rules(session, library.id)

    def remove_library_exclude_rule(
        self, library_key: str, pattern: str, *, actor_subject: str
    ) -> list[LibraryExcludeRuleRecord] | None:
        with self._session_factory.begin() as session:
            library = self._library(session, library_key)
            if library is None:
                return None
            rule = session.scalar(
                select(LibraryExcludeRule).where(
                    LibraryExcludeRule.library_id == library.id, LibraryExcludeRule.pattern == pattern
                )
            )
            if rule is None:
                return None
            session.delete(rule)
            self._audit(
                session,
                actor_subject,
                "remove_library_exclude_rule",
                None,
                {"library_key": library.key, "pattern": pattern},
            )
            session.flush()
            return self._list_library_exclude_rules(session, library.id)

    def get_appearance_preference(self, subject: str) -> str | None:
        with self._session_factory() as session:
            preference = session.scalar(
                select(UserPreference).where(UserPreference.subject == subject, UserPreference.key == "appearance")
            )
            appearance = preference.value.get("appearance") if preference is not None and isinstance(preference.value, dict) else None
            return appearance if appearance in {"dark", "light", "system"} else None

    def set_appearance_preference(self, subject: str, appearance: str) -> str:
        with self._session_factory.begin() as session:
            preference = session.scalar(
                select(UserPreference).where(UserPreference.subject == subject, UserPreference.key == "appearance")
            )
            if preference is None:
                session.add(UserPreference(subject=subject, key="appearance", value={"appearance": appearance}))
            else:
                preference.value = {"appearance": appearance}
        return appearance

    def get_explorer_preference(self, subject: str) -> tuple[str, int] | None:
        with self._session_factory() as session:
            preference = session.scalar(
                select(UserPreference).where(UserPreference.subject == subject, UserPreference.key == "explorer")
            )
            value = preference.value if preference is not None and isinstance(preference.value, dict) else {}
            view = value.get("view")
            page_size = value.get("page_size")
            if view not in {"grid", "list"} or page_size not in {25, 50, 100}:
                return None
            return view, page_size

    def set_explorer_preference(self, subject: str, view: str, page_size: int) -> tuple[str, int]:
        with self._session_factory.begin() as session:
            preference = session.scalar(
                select(UserPreference).where(UserPreference.subject == subject, UserPreference.key == "explorer")
            )
            value = {"view": view, "page_size": page_size}
            if preference is None:
                session.add(UserPreference(subject=subject, key="explorer", value=value))
            else:
                preference.value = value
        return view, page_size

    def list_assets(self, query: AssetQuery) -> list[AssetRecord]:
        return list(self.list_asset_page(query, limit=1_000_000, offset=0).items)

    def list_asset_page(self, query: AssetQuery, *, limit: int, offset: int) -> AssetPage:
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
            if query.project_id is not None:
                project = self._get_project(session, query.project_id)
                if project is None:
                    raise ValueError("project scope is invalid")
                statement = statement.join(ProjectAsset, ProjectAsset.asset_id == Asset.id).where(
                    ProjectAsset.project_id == project.id
                )
                if query.folder_id is not None:
                    try:
                        folder_key = int(query.folder_id)
                    except (TypeError, ValueError) as error:
                        raise ValueError("folder scope is invalid") from error
                    folder = session.get(ProjectFolder, folder_key)
                    if folder is None or folder.project_id != project.id:
                        raise ValueError("folder scope is invalid")
                    statement = statement.where(ProjectAsset.folder_id == folder.id)
            elif query.folder_id is not None:
                raise ValueError("folder scope requires a project")
            total = session.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
            assets = session.scalars(statement.order_by(Asset.relative_path, Asset.id).offset(offset).limit(limit)).all()
            return AssetPage(items=tuple(self._asset_record(asset) for asset in assets), total=total)

    def asset_summary(self, query: AssetQuery) -> dict[str, object]:
        with self._session_factory() as session:
            statement: Select[tuple[Asset]] = select(Asset).join(Asset.library)
            if query.library is None:
                statement = statement.where(Library.key != _ARCHIVE_LIBRARY_KEY)
            else:
                statement = statement.where(Library.key == query.library)
            if query.project_id is not None:
                project = self._get_project(session, query.project_id)
                if project is None:
                    raise ValueError("project scope is invalid")
                statement = statement.join(ProjectAsset, ProjectAsset.asset_id == Asset.id).where(ProjectAsset.project_id == project.id)
                if query.folder_id is not None:
                    try:
                        folder_key = int(query.folder_id)
                    except (TypeError, ValueError) as error:
                        raise ValueError("folder scope is invalid") from error
                    folder = session.get(ProjectFolder, folder_key)
                    if folder is None or folder.project_id != project.id:
                        raise ValueError("folder scope is invalid")
                    statement = statement.where(ProjectAsset.folder_id == folder.id)
            elif query.folder_id is not None:
                raise ValueError("folder scope requires a project")
            scoped = statement.order_by(None).subquery()
            total, size_bytes = session.execute(
                select(func.count(scoped.c.id), func.coalesce(func.sum(scoped.c.byte_size), 0))
            ).one()
            formats = {
                str(format_name): int(count)
                for format_name, count in session.execute(select(scoped.c.format, func.count()).group_by(scoped.c.format)).all()
            }
            return {"total": total, "size_bytes": size_bytes, "formats": formats}

    def get_asset(self, asset_id: str) -> AssetRecord | None:
        with self._session_factory() as session:
            asset = self._get_asset(session, asset_id)
            return self._asset_record(asset) if asset is not None else None

    def list_tags(self) -> list[TagRecord]:
        with self._session_factory() as session:
            tags = session.scalars(select(Tag).order_by(Tag.key)).all()
            return [TagRecord(key=tag.key, name=tag.name) for tag in tags]

    def create_tag(self, key: str, name: str, *, actor_subject: str) -> TagRecord:
        with self._session_factory.begin() as session:
            if session.scalar(select(Tag).where(Tag.key == key)) is not None:
                raise ValueError("tag already exists")
            tag = Tag(key=key, name=name.strip())
            session.add(tag)
            self._audit(session, actor_subject, "create_tag", None, {"tag_key": key})
            return TagRecord(key=tag.key, name=tag.name)

    def list_projects(self) -> list[ProjectRecord]:
        with self._session_factory() as session:
            projects = session.scalars(select(Project).options(selectinload(Project.assets), selectinload(Project.folders), selectinload(Project.asset_links)).order_by(Project.name)).all()
            return [self._project_record(project) for project in projects]

    def create_project(self, name: str, description: str, *, actor_subject: str) -> ProjectRecord:
        with self._session_factory.begin() as session:
            project = Project(name=name, description=description)
            session.add(project)
            session.flush()
            self._audit(session, actor_subject, "create_project", None, {"project_id": str(project.id), "name": project.name})
            return self._project_record(project)

    def create_project_folder(self, project_id: str, name: str, parent_id: str | None, *, actor_subject: str) -> ProjectFolderRecord | None:
        with self._session_factory.begin() as session:
            project = self._get_project(session, project_id)
            if project is None:
                return None
            parent = None if parent_id is None else session.get(ProjectFolder, int(parent_id))
            if parent_id is not None and parent is None:
                return None
            if parent is not None and parent.project_id != project.id:
                raise ValueError("parent folder belongs to another project")
            folder = ProjectFolder(project_id=project.id, parent_id=None if parent is None else parent.id, name=name)
            session.add(folder)
            session.flush()
            self._audit(session, actor_subject, "create_project_folder", None, {"project_id": str(project.id), "folder_id": str(folder.id)})
            return ProjectFolderRecord(id=str(folder.id), name=folder.name, parent_id=str(folder.parent_id) if folder.parent_id is not None else None)

    def assign_project_asset(self, project_id: str, asset_id: str, *, folder_id: str | None = None, actor_subject: str) -> ProjectRecord | None:
        with self._session_factory.begin() as session:
            project = self._get_project(session, project_id)
            asset = self._get_asset(session, asset_id)
            if project is None or asset is None:
                return None
            folder = None if folder_id is None else session.get(ProjectFolder, int(folder_id))
            if folder_id is not None and (folder is None or folder.project_id != project.id):
                return None
            link = next((link for link in project.asset_links if link.asset_id == asset.id), None)
            if link is None:
                link = ProjectAsset(project=project, asset=asset)
                session.add(link)
                session.flush()
            link.folder_id = None if folder is None else folder.id
            self._audit(session, actor_subject, "assign_project_asset", asset, {"project_id": str(project.id), "folder_id": str(link.folder_id) if link.folder_id is not None else None})
            session.flush()
            # ``project.assets`` was eagerly loaded before a first link existed.
            # Flush writes the link, but does not refresh that loaded collection,
            # so the immediate API response otherwise looks unassigned until a
            # second request reloads the project.
            session.expire(project, ["assets", "asset_links"])
            return self._project_record(project)

    def assign_project_assets_batch(self, project_id: str, asset_ids: tuple[str, ...], *, folder_id: str | None = None, actor_subject: str) -> ProjectRecord | None:
        with self._session_factory.begin() as session:
            project = self._get_project(session, project_id)
            if project is None:
                return None
            folder = None if folder_id is None else session.get(ProjectFolder, int(folder_id))
            if folder_id is not None and (folder is None or folder.project_id != project.id):
                return None
            assets = session.scalars(select(Asset).where(Asset.id.in_(asset_ids))).all()
            if len(assets) != len(asset_ids):
                return None
            by_id = {str(asset.id): asset for asset in assets}
            existing_by_asset_id = {str(link.asset_id): link for link in project.asset_links}
            for asset_id in asset_ids:
                asset = by_id[asset_id]
                link = existing_by_asset_id.get(asset_id)
                if link is None:
                    link = ProjectAsset(project=project, asset=asset)
                    session.add(link)
                link.folder_id = None if folder is None else folder.id
                self._audit(session, actor_subject, "batch_assign_project_asset", asset, {"project_id": str(project.id), "folder_id": str(link.folder_id) if link.folder_id is not None else None})
            session.flush()
            session.expire(project, ["assets", "asset_links"])
            return self._project_record(project)

    def remove_project_asset(self, project_id: str, asset_id: str, *, actor_subject: str) -> ProjectRecord | None:
        with self._session_factory.begin() as session:
            project = self._get_project(session, project_id)
            asset = self._get_asset(session, asset_id)
            if project is None or asset is None:
                return None
            link = next((link for link in project.asset_links if link.asset_id == asset.id), None)
            if link is None:
                return self._project_record(project)
            project.asset_links.remove(link)
            self._audit(session, actor_subject, "remove_project_asset", asset, {"project_id": str(project.id)})
            session.flush()
            session.expire(project, ["assets", "asset_links"])
            return self._project_record(project)

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

    def set_tags_batch(self, asset_ids: tuple[str, ...], tag_keys: set[str], *, actor_subject: str) -> list[AssetRecord] | None:
        with self._session_factory.begin() as session:
            assets = session.scalars(
                select(Asset).options(joinedload(Asset.library), selectinload(Asset.tags)).where(Asset.id.in_(asset_ids))
            ).all()
            if len(assets) != len(asset_ids):
                return None
            tags = list(session.scalars(select(Tag).where(Tag.key.in_(tag_keys))).all())
            if len(tags) != len(tag_keys):
                return None
            by_id = {str(asset.id): asset for asset in assets}
            ordered_assets = [by_id[asset_id] for asset_id in asset_ids]
            for asset in ordered_assets:
                asset.tags = tags
                self._audit(session, actor_subject, "batch_assign_tags", asset, {"tag_keys": sorted(tag_keys)})
            session.flush()
            return [self._asset_record(asset) for asset in ordered_assets]

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

    def archive_batch(self, asset_ids: tuple[str, ...], *, actor_subject: str) -> list[AssetRecord] | None:
        with self._session_factory.begin() as session:
            assets = session.scalars(
                select(Asset).options(joinedload(Asset.library), selectinload(Asset.tags)).where(Asset.id.in_(asset_ids))
            ).all()
            if len(assets) != len(asset_ids):
                return None
            by_id = {str(asset.id): asset for asset in assets}
            ordered_assets = [by_id[asset_id] for asset_id in asset_ids]
            if any(asset.library.key == _ARCHIVE_LIBRARY_KEY for asset in ordered_assets):
                return None
            archive_library = self._library(session, _ARCHIVE_LIBRARY_KEY)
            if archive_library is None:
                raise RuntimeError("the configured archive library is unavailable")

            for asset in ordered_assets:
                self._prevalidate_archive_asset(asset)

            completed: list[ArchiveMetadata] = []
            try:
                for asset in ordered_assets:
                    result = self._archive_service.archive_asset(asset)
                    completed.append(result.metadata)
                    asset.library = archive_library
                    asset.relative_path = result.destination_relative_path
                    self._audit(session, actor_subject, "archive", asset, result.audit_metadata())
                session.flush()
            except Exception:
                for metadata in reversed(completed):
                    try:
                        self._archive_service.restore(metadata)
                    except Exception:
                        break
                raise
            return [self._asset_record(asset) for asset in ordered_assets]

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

    def upload(
        self, library_key: str, filename: str, stream: BinaryIO, *, actor_subject: str, collision_policy: str = "reject"
    ) -> AssetRecord:
        if library_key == _ARCHIVE_LIBRARY_KEY:
            raise ValueError("archive uploads are not allowed")
        destination: Path | None = None
        created = False
        backup: Path | None = None
        try:
            with self._session_factory.begin() as session:
                library = self._library(session, library_key)
                if library is None:
                    raise ValueError("unknown library")
                asset = session.scalar(
                    select(Asset).where(Asset.library_id == library.id, Asset.relative_path == filename)
                )
                if asset is not None:
                    if collision_policy == "reject":
                        raise PathCollisionError("destination already exists")
                    if collision_policy == "rename":
                        filename, destination = self._prepare_renamed_destination(library, filename)
                        self._write_upload(stream, destination)
                        created = True
                        asset = None
                    elif collision_policy == "overwrite":
                        destination = self._filesystem.resolve_asset(asset, require_regular=True)
                        backup = self._replace_upload(stream, destination)
                    else:
                        raise ValueError("invalid collision policy")
                else:
                    destination = self._filesystem.prepare_destination(library, filename)
                    self._write_upload(stream, destination)
                    created = True
                assert destination is not None
                fingerprint = fingerprint_model(destination)
                self._thumbnails.create(destination, fingerprint)
                metadata = _three_mf_metadata(destination, fingerprint.format)
                if asset is None:
                    asset = Asset(
                        library=library,
                        relative_path=filename,
                        format=model_format(destination),
                        byte_size=fingerprint.byte_size,
                        sha256=fingerprint.sha256,
                        metadata_json=metadata,
                    )
                    session.add(asset)
                    action = "upload"
                else:
                    asset.format = model_format(destination)
                    asset.byte_size = fingerprint.byte_size
                    asset.sha256 = fingerprint.sha256
                    asset.metadata_json = metadata
                    action = "overwrite_upload"
                session.flush()
                self._audit(session, actor_subject, action, asset, {"filename": filename, "byte_size": fingerprint.byte_size})
                record = self._asset_record(asset)
        except Exception:
            if backup is not None and destination is not None:
                destination.unlink(missing_ok=True)
                os.replace(backup, destination)
            elif created and destination is not None:
                destination.unlink(missing_ok=True)
            raise
        if backup is not None:
            backup.unlink(missing_ok=True)
        return record

    def _prepare_renamed_destination(self, library: Library, filename: str) -> tuple[str, Path]:
        stem, suffix = os.path.splitext(filename)
        for number in range(1, 10_001):
            candidate = f"{stem} ({number}){suffix}"
            try:
                return candidate, self._filesystem.prepare_destination(library, candidate)
            except PathCollisionError:
                continue
        raise ValueError("could not find an available filename")

    @classmethod
    def _replace_upload(cls, stream: BinaryIO, destination: Path) -> Path:
        staged = cls._stage_upload(stream, destination.parent)
        descriptor, backup_name = tempfile.mkstemp(prefix=".printvault-backup-", dir=destination.parent)
        os.close(descriptor)
        backup = Path(backup_name)
        backup.unlink(missing_ok=True)
        try:
            os.replace(destination, backup)
            os.replace(staged, destination)
        except Exception:
            staged.unlink(missing_ok=True)
            if backup.exists():
                os.replace(backup, destination)
            raise
        return backup

    @staticmethod
    def _stage_upload(stream: BinaryIO, parent: Path, *, max_bytes: int = 512 * 1024 * 1024) -> Path:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".printvault-upload-", dir=parent)
        temporary = Path(temporary_name)
        written = 0
        try:
            stream.seek(0)
            with os.fdopen(descriptor, "wb") as output:
                while chunk := stream.read(64 * 1024):
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError("upload exceeds configured limit")
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            return temporary
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def upload_thumbnail(
        self, asset_id: str, stream: BinaryIO, content_type: str | None, *, actor_subject: str
    ) -> AssetRecord | None:
        previous_digest: str | None = None
        replacement_digest: str | None = None
        with self._session_factory.begin() as session:
            asset = self._get_asset(session, asset_id)
            if asset is None:
                return None
            previous_digest = asset.manual_thumbnail_sha
            thumbnail = self._thumbnails.store_manual(stream, content_type)
            if thumbnail.sha256 is None:
                raise RuntimeError("manual thumbnail storage did not return a digest")
            replacement_digest = thumbnail.sha256
            asset.manual_thumbnail_sha = replacement_digest
            self._audit(
                session,
                actor_subject,
                "upload_thumbnail",
                asset,
                {"byte_size": thumbnail.path.stat().st_size},
            )
            session.flush()
            record = self._asset_record(asset)
        if previous_digest and previous_digest != replacement_digest:
            with self._session_factory() as session:
                still_referenced = session.scalar(
                    select(Asset.id).where(Asset.manual_thumbnail_sha == previous_digest).limit(1)
                ) is not None
            if not still_referenced:
                self._thumbnails.remove_manual(previous_digest)
        return record

    def issue_pairing_code(self, *, actor_subject: str, now: datetime) -> PairingCodeRecord:
        with self._session_factory.begin() as session:
            code = self._new_pairing_code()
            expires_at = now + timedelta(minutes=5)
            session.add(
                HelperPairingCode(
                    owner_subject=actor_subject,
                    code_hash=self._token_hash(code),
                    expires_at=expires_at,
                )
            )
            self._audit(
                session,
                actor_subject,
                "issue_helper_pairing_code",
                None,
                {"expires_at": expires_at.isoformat()},
            )
            return PairingCodeRecord(code=code, expires_at=expires_at)

    def register_helper_device(
        self, *, pairing_code: str, device_name: str, now: datetime
    ) -> HelperDeviceRecord:
        code_hash = self._token_hash(pairing_code)
        with self._session_factory.begin() as session:
            pairing = session.scalar(
                select(HelperPairingCode).where(HelperPairingCode.code_hash == code_hash)
            )
            if pairing is None:
                self._audit(session, "anonymous", "deny_helper_device_registration", None, {"reason": "invalid_pairing_code"})
                raise ValueError("pairing code is invalid")
            if pairing.redeemed_at is not None or _is_expired(pairing.expires_at, now):
                self._audit(
                    session,
                    pairing.owner_subject,
                    "deny_helper_device_registration",
                    None,
                    {"reason": "expired_pairing_code"},
                )
                raise ValueError("pairing code has expired")
            device_id = self._new_device_id()
            credential = self._new_secret_token()
            device = HelperDevice(
                device_id=device_id,
                owner_subject=pairing.owner_subject,
                name=device_name.strip(),
                credential_hash=self._token_hash(credential),
            )
            pairing.redeemed_at = now
            session.add(device)
            self._audit(
                session,
                pairing.owner_subject,
                "register_helper_device",
                None,
                {"device_id": device_id, "name": device.name},
            )
            return HelperDeviceRecord(
                device_id=device.device_id,
                owner_subject=device.owner_subject,
                name=device.name,
                credential=credential,
                created_at=device.created_at,
            )

    def list_helper_devices(self, *, actor_subject: str) -> list[HelperDeviceRecord]:
        with self._session_factory() as session:
            devices = session.scalars(
                select(HelperDevice)
                .where(HelperDevice.owner_subject == actor_subject)
                .order_by(HelperDevice.created_at.desc(), HelperDevice.id.desc())
            ).all()
            return [
                HelperDeviceRecord(
                    device_id=device.device_id,
                    owner_subject=device.owner_subject,
                    name=device.name,
                    created_at=device.created_at,
                )
                for device in devices
            ]

    def revoke_helper_device(self, *, actor_subject: str, device_id: str) -> bool:
        with self._session_factory.begin() as session:
            device = session.scalar(
                select(HelperDevice).where(
                    HelperDevice.device_id == device_id,
                    HelperDevice.owner_subject == actor_subject,
                )
            )
            if device is None:
                return False
            session.delete(device)
            self._audit(session, actor_subject, "revoke_helper_device", None, {"device_id": device_id, "name": device.name})
            return True

    def authenticate_helper_device(self, credential: str) -> HelperDevicePrincipal | None:
        with self._session_factory() as session:
            device = session.scalar(
                select(HelperDevice).where(HelperDevice.credential_hash == self._token_hash(credential))
            )
            if device is None:
                return None
            return HelperDevicePrincipal(device_id=device.device_id, owner_subject=device.owner_subject)

    def create_helper_job(
        self,
        *,
        actor_subject: str,
        device_id: str,
        asset_id: str,
        profile_id: str,
        expires_in_seconds: int,
        now: datetime,
    ) -> HelperJobRecord | None:
        if expires_in_seconds < 1 or expires_in_seconds > 300:
            raise ValueError("helper job expiry must be between one second and five minutes")
        with self._session_factory.begin() as session:
            device = session.scalar(select(HelperDevice).where(HelperDevice.device_id == device_id))
            asset = self._get_asset(session, asset_id)
            if device is None or asset is None or device.owner_subject != actor_subject or not asset.sha256:
                return None
            request_id = self._new_request_id()
            expires_at = now + timedelta(seconds=expires_in_seconds)
            session.add(
                HelperJob(
                    owner_subject=actor_subject,
                    request_id_hash=self._token_hash(request_id),
                    device_id=device.id,
                    asset_id=asset.id,
                    profile_id=profile_id,
                    expires_at=expires_at,
                )
            )
            self._audit(
                session,
                actor_subject,
                "create_helper_job",
                asset,
                {"device_id": device.device_id, "profile_id": profile_id, "expires_at": expires_at.isoformat()},
            )
            return HelperJobRecord(
                request_id=request_id,
                profile_id=profile_id,
                user_id=actor_subject,
                device_id=device.device_id,
                asset_id=str(asset.id),
                asset_name=Path(asset.relative_path).name,
                asset_sha256=asset.sha256,
                expires_at=expires_at,
            )

    def redeem_helper_job(
        self,
        *,
        request_id: str,
        device_id: str,
        user_id: str,
        now: datetime,
        origin: str,
    ) -> HelperJobAccess | None:
        request_hash = self._token_hash(request_id)
        with self._session_factory.begin() as session:
            device = session.scalar(
                select(HelperDevice).where(
                    HelperDevice.device_id == device_id,
                    HelperDevice.owner_subject == user_id,
                )
            )
            if device is None:
                self._audit(session, user_id, "deny_helper_job_redeem", None, {"device_id": device_id, "reason": "unknown_device"})
                return None
            updated = session.execute(
                update(HelperJob)
                .where(
                    HelperJob.request_id_hash == request_hash,
                    HelperJob.device_id == device.id,
                    HelperJob.owner_subject == user_id,
                    HelperJob.redeemed_at.is_(None),
                    HelperJob.expires_at > now,
                )
                .values(redeemed_at=now)
            )
            if updated.rowcount != 1:
                self._audit(session, user_id, "deny_helper_job_redeem", None, {"device_id": device_id, "reason": "not_found_or_consumed"})
                return None
            job = session.scalar(
                select(HelperJob)
                .options(joinedload(HelperJob.device), joinedload(HelperJob.asset))
                .where(HelperJob.request_id_hash == request_hash)
            )
            if (
                job is None
                or job.asset.sha256 is None
            ):
                self._audit(session, user_id, "deny_helper_job_redeem", None, {"device_id": device_id, "reason": "binding_mismatch"})
                return None
            self._audit(
                session,
                user_id,
                "redeem_helper_job",
                job.asset,
                {"device_id": device_id, "profile_id": job.profile_id},
            )
            return HelperJobAccess(
                request_id=request_id,
                profile_id=job.profile_id,
                user_id=job.owner_subject,
                device_id=job.device.device_id,
                asset_url=f"{origin}/api/helper/jobs/{request_id}/asset",
                asset_name=Path(job.asset.relative_path).name,
                asset_sha256=job.asset.sha256,
                expires_at=job.expires_at,
            )

    def open_helper_job_asset(
        self, *, request_id: str, device_id: str, owner_subject: str, now: datetime
    ) -> DownloadHandle | None:
        request_hash = self._token_hash(request_id)
        with self._session_factory() as session:
            job = session.scalar(
                select(HelperJob)
                .options(joinedload(HelperJob.device), joinedload(HelperJob.asset).joinedload(Asset.library))
                .where(HelperJob.request_id_hash == request_hash)
            )
            if (
                job is None
                or job.redeemed_at is None
                or _is_expired(job.expires_at, now)
                or job.owner_subject != owner_subject
                or job.device.device_id != device_id
            ):
                return None
            try:
                path = self._filesystem.resolve_asset(job.asset, require_regular=True)
                stream = path.open("rb")
            except (OSError, UnsafePathError):
                return None
            return DownloadHandle(
                filename=Path(job.asset.relative_path).name,
                content_type=_CONTENT_TYPES.get(job.asset.format.casefold(), "application/octet-stream"),
                stream=stream,
            )

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

    def open_thumbnail(self, asset_id: str) -> DownloadHandle | None:
        with self._session_factory() as session:
            asset = self._get_asset(session, asset_id)
            if asset is None:
                return None
            if asset.manual_thumbnail_sha:
                manual = self._thumbnails.manual_candidate(asset.manual_thumbnail_sha)
                if manual is not None:
                    candidate, content_type = manual
                    try:
                        return DownloadHandle(filename=candidate.name, content_type=content_type, stream=candidate.open("rb"))
                    except OSError:
                        return None
            if not asset.sha256:
                return None
            for suffix, content_type in ((".png", "image/png"), (".jpg", "image/jpeg"), (".jpeg", "image/jpeg"), (".webp", "image/webp"), (".svg", "image/svg+xml")):
                candidate = self._thumbnails.root / asset.sha256[:2] / f"{asset.sha256}{suffix}"
                try:
                    if candidate.is_file() and not candidate.is_symlink():
                        return DownloadHandle(filename=candidate.name, content_type=content_type, stream=candidate.open("rb"))
                except OSError:
                    continue
            return None

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
                metadata=dict(asset.metadata_json or {}),
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
                    metadata_json=indexed.metadata,
                )
                session.add(asset)
            elif not create_only:
                asset.format = indexed.format
                asset.byte_size = indexed.fingerprint.byte_size
                asset.sha256 = indexed.fingerprint.sha256
                asset.metadata_json = indexed.metadata

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
            metadata=dict(asset.metadata_json or {}),
        )

    @staticmethod
    def _project_record(project: Project) -> ProjectRecord:
        return ProjectRecord(
            id=str(project.id),
            name=project.name,
            description=project.description,
            asset_ids=tuple(str(asset.id) for asset in sorted(project.assets, key=lambda asset: asset.id)),
            folders=tuple(ProjectFolderRecord(id=str(folder.id), name=folder.name, parent_id=str(folder.parent_id) if folder.parent_id is not None else None) for folder in sorted(project.folders, key=lambda folder: folder.id)),
            asset_folder_ids={str(link.asset_id): str(link.folder_id) for link in project.asset_links if link.folder_id is not None},
        )

    @staticmethod
    def _get_project(session: Session, project_id: str) -> Project | None:
        try:
            primary_key = int(project_id)
        except (TypeError, ValueError):
            return None
        return session.scalar(select(Project).options(selectinload(Project.assets), selectinload(Project.folders), selectinload(Project.asset_links)).where(Project.id == primary_key))

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

    def _token_hash(self, token: str) -> str:
        return hmac.new(self._helper_secret, token.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _new_secret_token() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def _new_device_id() -> str:
        return f"device_{secrets.token_urlsafe(12)}"

    @staticmethod
    def _new_request_id() -> str:
        return f"req_{secrets.token_urlsafe(18)}"

    @staticmethod
    def _new_pairing_code() -> str:
        return base64.b32encode(secrets.token_bytes(5)).decode("ascii").rstrip("=")

    @staticmethod
    def _audit(session: Session, actor_subject: str, action: str, asset: Asset | None, metadata: dict[str, Any]) -> None:
        session.add(AuditEvent(actor_subject=actor_subject, action=action, asset=asset, metadata_json=metadata))

    def _prevalidate_archive_asset(self, asset: Asset) -> None:
        source_identity = self._archive_service.registry.register_library(asset.library)
        if source_identity.key == _ARCHIVE_LIBRARY_KEY:
            raise UnsafePathError("assets already in the archive cannot be archived again")
        source_relative_path = asset.relative_path
        self._archive_service.filesystem.resolve_asset(asset, require_regular=True)
        archive_relative_path = f"{source_identity.key}/{source_relative_path}"
        destination = self._archive_service.filesystem.resolve_library_path(
            self._archive_service.archive_library,
            archive_relative_path,
        )
        if os.path.lexists(destination):
            raise PathCollisionError("destination already exists")

        archive_root = self._archive_service.registry.root_for_key(_ARCHIVE_LIBRARY_KEY)
        parent = archive_root
        for part in Path(archive_relative_path).parent.parts:
            if part in ("", "."):
                continue
            parent = parent / part
            if not os.path.lexists(parent):
                continue
            mode = os.lstat(parent).st_mode
            if stat.S_ISLNK(mode):
                raise UnsafePathError("symlink components are not permitted")
            if not stat.S_ISDIR(mode):
                raise UnsafePathError("destination parent is not a directory")

    @staticmethod
    def _audit_asset_id(event: AuditEvent) -> str | None:
        asset_id = event.metadata_json.get("asset_id") if isinstance(event.metadata_json, dict) else None
        return asset_id if isinstance(asset_id, str) else None

    @staticmethod
    def _list_library_exclude_rules(session: Session, library_id: int) -> list[LibraryExcludeRuleRecord]:
        return [
            LibraryExcludeRuleRecord(pattern=pattern)
            for pattern in session.scalars(
                select(LibraryExcludeRule.pattern)
                .where(LibraryExcludeRule.library_id == library_id)
                .order_by(LibraryExcludeRule.pattern, LibraryExcludeRule.id)
            ).all()
        ]

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
