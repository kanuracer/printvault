"""Authenticated, dependency-injected asset API for PrintVault.

This module intentionally has no database or filesystem-global dependency.  The
application composition root supplies a repository and a BFF-session resolver
via :func:`register_api`; production wiring can adapt existing persistence and
archive services without exposing host paths to HTTP callers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import PurePosixPath, PureWindowsPath
from typing import BinaryIO, Literal, Protocol
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import normalize_relative_glob_pattern
from app.services.rbac import ROLE_CAPABILITIES
from app.services.filesystem import PathCollisionError, UnsafePathError
from app.services.metadata import SUPPORTED_FORMATS
from app.services.thumbnails import ThumbnailCache


@dataclass(frozen=True)
class ApiSession:
    """Minimum BFF-authenticated identity accepted by this API boundary."""

    subject: str
    role: str | None


@dataclass(frozen=True)
class ApiActor:
    """Server-derived role and capabilities for one authenticated request."""

    subject: str
    role: str
    capabilities: frozenset[str]


@dataclass(frozen=True)
class LibraryRecord:
    key: str
    name: str


@dataclass(frozen=True)
class LibraryExcludeRuleRecord:
    pattern: str


@dataclass
class AssetRecord:
    id: str
    library_key: str
    relative_path: str
    format: str
    favorite: bool = False
    tags: set[str] = field(default_factory=set)
    archived: bool = False
    byte_size: int | None = None
    content: bytes = b""
    content_type: str = "application/octet-stream"
    manual_thumbnail_content: bytes = b""
    manual_thumbnail_content_type: str = "application/octet-stream"
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class TagRecord:
    key: str
    name: str


@dataclass(frozen=True)
class ProjectFolderRecord:
    id: str
    name: str
    parent_id: str | None


@dataclass(frozen=True)
class ProjectRecord:
    id: str
    name: str
    description: str
    asset_ids: tuple[str, ...] = ()
    folders: tuple[ProjectFolderRecord, ...] = ()
    asset_folder_ids: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DownloadHandle:
    """A service-issued stream; request data never names a filesystem path."""

    filename: str
    content_type: str
    stream: BinaryIO


@dataclass(frozen=True)
class AuditRecord:
    actor_subject: str
    action: str
    asset_id: str | None


@dataclass(frozen=True)
class AssetQuery:
    q: str | None = None
    favorite: bool | None = None
    library: str | None = None
    tag: str | None = None
    format: str | None = None
    project_id: str | None = None
    folder_id: str | None = None


@dataclass(frozen=True)
class AssetPage:
    items: tuple[AssetRecord, ...]
    total: int


@dataclass(frozen=True)
class HelperDeviceRecord:
    device_id: str
    owner_subject: str
    name: str
    credential: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class PairingCodeRecord:
    code: str
    expires_at: datetime


@dataclass(frozen=True)
class HelperJobRecord:
    request_id: str
    profile_id: str
    user_id: str
    device_id: str
    asset_id: str
    asset_name: str
    asset_sha256: str
    expires_at: datetime


@dataclass(frozen=True)
class HelperJobAccess:
    request_id: str
    profile_id: str
    user_id: str
    device_id: str
    asset_url: str
    asset_name: str
    asset_sha256: str
    expires_at: datetime


@dataclass(frozen=True)
class HelperDevicePrincipal:
    device_id: str
    owner_subject: str


class AssetRepository(Protocol):
    """Small persistence/service boundary used by the HTTP router."""

    def list_libraries(self) -> list[LibraryRecord]: ...

    def list_library_exclude_rules(self, library_key: str) -> list[LibraryExcludeRuleRecord] | None: ...

    def add_library_exclude_rule(
        self, library_key: str, pattern: str, *, actor_subject: str
    ) -> list[LibraryExcludeRuleRecord] | None: ...

    def remove_library_exclude_rule(
        self, library_key: str, pattern: str, *, actor_subject: str
    ) -> list[LibraryExcludeRuleRecord] | None: ...

    def get_appearance_preference(self, subject: str) -> str | None: ...

    def set_appearance_preference(self, subject: str, appearance: str) -> str: ...

    def get_explorer_preference(self, subject: str) -> tuple[str, int] | None: ...

    def set_explorer_preference(self, subject: str, view: str, page_size: int) -> tuple[str, int]: ...

    def list_assets(self, query: AssetQuery) -> list[AssetRecord]: ...

    def list_asset_page(self, query: AssetQuery, *, limit: int, offset: int) -> AssetPage: ...

    def asset_summary(self, query: AssetQuery) -> dict[str, object]: ...

    def get_asset(self, asset_id: str) -> AssetRecord | None: ...

    def list_tags(self) -> list[TagRecord]: ...

    def create_tag(self, key: str, name: str, *, actor_subject: str) -> TagRecord: ...

    def list_projects(self) -> list[ProjectRecord]: ...

    def create_project(self, name: str, description: str, *, actor_subject: str) -> ProjectRecord: ...

    def assign_project_asset(self, project_id: str, asset_id: str, *, folder_id: str | None = None, actor_subject: str) -> ProjectRecord | None: ...

    def assign_project_assets_batch(self, project_id: str, asset_ids: tuple[str, ...], *, folder_id: str | None = None, actor_subject: str) -> ProjectRecord | None: ...

    def remove_project_asset(self, project_id: str, asset_id: str, *, actor_subject: str) -> ProjectRecord | None: ...

    def create_project_folder(self, project_id: str, name: str, parent_id: str | None, *, actor_subject: str) -> ProjectFolderRecord | None: ...

    def set_tags(self, asset_id: str, tag_keys: set[str], *, actor_subject: str) -> AssetRecord | None: ...

    def set_tags_batch(self, asset_ids: tuple[str, ...], tag_keys: set[str], *, actor_subject: str) -> list[AssetRecord] | None: ...

    def set_favorite(self, asset_id: str, favorite: bool, *, actor_subject: str) -> AssetRecord | None: ...

    def archive(self, asset_id: str, *, actor_subject: str) -> AssetRecord | None: ...

    def archive_batch(self, asset_ids: tuple[str, ...], *, actor_subject: str) -> list[AssetRecord] | None: ...

    def restore(self, asset_id: str, *, actor_subject: str) -> AssetRecord | None: ...

    def move(
        self, asset_id: str, *, destination_library_key: str, destination_relative_path: str, actor_subject: str
    ) -> AssetRecord | None: ...

    def permanently_delete(self, asset_id: str, *, actor_subject: str) -> bool: ...

    def upload(
        self, library_key: str, filename: str, stream: BinaryIO, *, actor_subject: str, collision_policy: Literal["reject", "overwrite", "rename"] = "reject"
    ) -> AssetRecord: ...

    def upload_thumbnail(
        self, asset_id: str, stream: BinaryIO, content_type: str | None, *, actor_subject: str
    ) -> AssetRecord | None: ...

    def issue_pairing_code(self, *, actor_subject: str, now: datetime) -> PairingCodeRecord: ...

    def register_helper_device(
        self, *, pairing_code: str, device_name: str, now: datetime
    ) -> HelperDeviceRecord: ...

    def list_helper_devices(self, *, actor_subject: str) -> list[HelperDeviceRecord]: ...

    def revoke_helper_device(self, *, actor_subject: str, device_id: str) -> bool: ...

    def authenticate_helper_device(self, credential: str) -> HelperDevicePrincipal | None: ...

    def create_helper_job(
        self,
        *,
        actor_subject: str,
        device_id: str,
        asset_id: str,
        profile_id: str,
        expires_in_seconds: int,
        now: datetime,
    ) -> HelperJobRecord | None: ...

    def redeem_helper_job(
        self,
        *,
        request_id: str,
        device_id: str,
        user_id: str,
        now: datetime,
        origin: str,
    ) -> HelperJobAccess | None: ...

    def open_helper_job_asset(
        self, *, request_id: str, device_id: str, owner_subject: str, now: datetime
    ) -> DownloadHandle | None: ...

    def list_audit(self) -> list[AuditRecord]: ...

    def open_download(self, asset_id: str) -> DownloadHandle | None: ...

    def open_thumbnail(self, asset_id: str) -> DownloadHandle | None: ...


class InMemoryAssetRepository:
    """Simple injected test double; it deliberately has no host-path support."""

    def __init__(
        self,
        *,
        libraries: Iterable[LibraryRecord] = (),
        assets: Iterable[AssetRecord] = (),
        tags: Iterable[TagRecord] = (),
    ) -> None:
        self._libraries = {library.key: library for library in libraries}
        self._assets = {asset.id: asset for asset in assets}
        self._tags = {tag.key: tag for tag in tags}
        self._projects: dict[str, ProjectRecord] = {}
        self._library_exclude_rules: dict[str, set[str]] = {}
        self._appearance_preferences: dict[str, str] = {}
        self._explorer_preferences: dict[str, tuple[str, int]] = {}
        self._helper_pairing_codes: dict[str, tuple[str, datetime]] = {}
        self._helper_devices: dict[str, HelperDeviceRecord] = {}
        self._helper_credentials: dict[str, str] = {}
        self._helper_jobs: dict[str, HelperJobRecord] = {}
        self._helper_redeemed_jobs: set[str] = set()
        self._audit: list[AuditRecord] = []

    @classmethod
    def demo(cls) -> InMemoryAssetRepository:
        return cls(
            libraries=(LibraryRecord("models", "Models"), LibraryRecord("archive", "Archive")),
            tags=(TagRecord("art", "Art"), TagRecord("functional", "Functional")),
            assets=(
                AssetRecord(
                    id="asset-bracket",
                    library_key="models",
                    relative_path="functional/Bracket.stl",
                    format="stl",
                    favorite=True,
                    tags={"functional"},
                    content=b"solid bracket",
                    content_type="model/stl",
                ),
                AssetRecord(
                    id="asset-cube",
                    library_key="models",
                    relative_path="examples/Cube.obj",
                    format="obj",
                    tags={"art"},
                    content=b"o cube",
                    content_type="model/obj",
                ),
            ),
        )

    def list_libraries(self) -> list[LibraryRecord]:
        return sorted(self._libraries.values(), key=lambda library: library.key)

    def list_library_exclude_rules(self, library_key: str) -> list[LibraryExcludeRuleRecord] | None:
        if library_key not in self._libraries:
            return None
        return [LibraryExcludeRuleRecord(pattern=pattern) for pattern in sorted(self._library_exclude_rules.get(library_key, set()))]

    def add_library_exclude_rule(
        self, library_key: str, pattern: str, *, actor_subject: str
    ) -> list[LibraryExcludeRuleRecord] | None:
        if library_key not in self._libraries:
            return None
        normalized = normalize_relative_glob_pattern(pattern)
        rules = self._library_exclude_rules.setdefault(library_key, set())
        rules.add(normalized)
        self._record(actor_subject, "add_library_exclude_rule", None)
        return self.list_library_exclude_rules(library_key)

    def remove_library_exclude_rule(
        self, library_key: str, pattern: str, *, actor_subject: str
    ) -> list[LibraryExcludeRuleRecord] | None:
        if library_key not in self._libraries:
            return None
        normalized = normalize_relative_glob_pattern(pattern)
        rules = self._library_exclude_rules.setdefault(library_key, set())
        if normalized not in rules:
            return None
        rules.remove(normalized)
        self._record(actor_subject, "remove_library_exclude_rule", None)
        return self.list_library_exclude_rules(library_key)

    def get_appearance_preference(self, subject: str) -> str | None:
        return self._appearance_preferences.get(subject)

    def set_appearance_preference(self, subject: str, appearance: str) -> str:
        self._appearance_preferences[subject] = appearance
        return appearance

    def get_explorer_preference(self, subject: str) -> tuple[str, int] | None:
        return self._explorer_preferences.get(subject)

    def set_explorer_preference(self, subject: str, view: str, page_size: int) -> tuple[str, int]:
        preference = (view, page_size)
        self._explorer_preferences[subject] = preference
        return preference

    def list_assets(self, query: AssetQuery) -> list[AssetRecord]:
        assets = list(self._assets.values())
        if query.q:
            needle = query.q.casefold()
            assets = [asset for asset in assets if needle in asset.relative_path.casefold()]
        if query.favorite is not None:
            assets = [asset for asset in assets if asset.favorite is query.favorite]
        if query.library:
            assets = [asset for asset in assets if asset.library_key == query.library]
        if query.tag:
            assets = [asset for asset in assets if query.tag in asset.tags]
        if query.format:
            assets = [asset for asset in assets if asset.format == query.format.casefold()]
        if query.project_id:
            project = self._projects.get(query.project_id)
            if project is None:
                raise ValueError("project scope is invalid")
            assets = [asset for asset in assets if asset.id in project.asset_ids]
            if query.folder_id is not None:
                if query.folder_id not in {folder.id for folder in project.folders}:
                    raise ValueError("folder scope is invalid")
                assets = [asset for asset in assets if project.asset_folder_ids.get(asset.id) == query.folder_id]
        elif query.folder_id is not None:
            raise ValueError("folder scope requires a project")
        return sorted(assets, key=lambda asset: asset.id)

    def list_asset_page(self, query: AssetQuery, *, limit: int, offset: int) -> AssetPage:
        assets = self.list_assets(query)
        return AssetPage(items=tuple(assets[offset : offset + limit]), total=len(assets))

    def asset_summary(self, query: AssetQuery) -> dict[str, object]:
        assets = self.list_assets(query)
        formats: dict[str, int] = {}
        for asset in assets:
            formats[asset.format] = formats.get(asset.format, 0) + 1
        return {"total": len(assets), "size_bytes": sum(asset.byte_size or 0 for asset in assets), "formats": formats}

    def get_asset(self, asset_id: str) -> AssetRecord | None:
        return self._assets.get(asset_id)

    def list_tags(self) -> list[TagRecord]:
        return sorted(self._tags.values(), key=lambda tag: tag.key)

    def list_projects(self) -> list[ProjectRecord]:
        return sorted(self._projects.values(), key=lambda project: project.name.casefold())

    def create_project(self, name: str, description: str, *, actor_subject: str) -> ProjectRecord:
        normalized_name = name.strip()
        if not normalized_name or any(project.name.casefold() == normalized_name.casefold() for project in self._projects.values()):
            raise ValueError("invalid project")
        project = ProjectRecord(id=f"project-{len(self._projects) + 1}", name=normalized_name, description=description.strip())
        self._projects[project.id] = project
        self._record(actor_subject, "create_project", None)
        return project

    def assign_project_asset(self, project_id: str, asset_id: str, *, folder_id: str | None = None, actor_subject: str) -> ProjectRecord | None:
        project = self._projects.get(project_id)
        if project is None or asset_id not in self._assets:
            return None
        if folder_id is not None and folder_id not in {folder.id for folder in project.folders}:
            return None
        folder_ids = dict(project.asset_folder_ids)
        if folder_id is None:
            folder_ids.pop(asset_id, None)
        else:
            folder_ids[asset_id] = folder_id
        updated = ProjectRecord(
            id=project.id,
            name=project.name,
            description=project.description,
            asset_ids=tuple(sorted({*project.asset_ids, asset_id})),
            folders=project.folders,
            asset_folder_ids=folder_ids,
        )
        self._projects[project_id] = updated
        self._record(actor_subject, "assign_project_asset", asset_id)
        return updated

    def assign_project_assets_batch(self, project_id: str, asset_ids: tuple[str, ...], *, folder_id: str | None = None, actor_subject: str) -> ProjectRecord | None:
        project = self._projects.get(project_id)
        if project is None or any(asset_id not in self._assets for asset_id in asset_ids):
            return None
        if folder_id is not None and folder_id not in {folder.id for folder in project.folders}:
            return None
        folder_ids = dict(project.asset_folder_ids)
        for asset_id in asset_ids:
            if folder_id is None:
                folder_ids.pop(asset_id, None)
            else:
                folder_ids[asset_id] = folder_id
        updated = ProjectRecord(
            id=project.id,
            name=project.name,
            description=project.description,
            asset_ids=tuple(sorted({*project.asset_ids, *asset_ids})),
            folders=project.folders,
            asset_folder_ids=folder_ids,
        )
        self._projects[project_id] = updated
        for asset_id in asset_ids:
            self._record(actor_subject, "batch_assign_project_asset", asset_id)
        return updated

    def create_project_folder(self, project_id: str, name: str, parent_id: str | None, *, actor_subject: str) -> ProjectFolderRecord | None:
        project = self._projects.get(project_id)
        normalized_name = name.strip()
        if project is None or not normalized_name or "/" in normalized_name or "\\" in normalized_name:
            return None
        if parent_id is not None and parent_id not in {folder.id for folder in project.folders}:
            return None
        if any(folder.parent_id == parent_id and folder.name.casefold() == normalized_name.casefold() for folder in project.folders):
            raise ValueError("project folder already exists")
        folder = ProjectFolderRecord(
            id=f"folder-{sum(len(item.folders) for item in self._projects.values()) + 1}",
            name=normalized_name,
            parent_id=parent_id,
        )
        self._projects[project_id] = ProjectRecord(
            id=project.id,
            name=project.name,
            description=project.description,
            asset_ids=project.asset_ids,
            folders=(*project.folders, folder),
            asset_folder_ids=dict(project.asset_folder_ids),
        )
        self._record(actor_subject, "create_project_folder", None)
        return folder

    def remove_project_asset(self, project_id: str, asset_id: str, *, actor_subject: str) -> ProjectRecord | None:
        project = self._projects.get(project_id)
        if project is None or asset_id not in self._assets:
            return None
        updated = ProjectRecord(
            id=project.id,
            name=project.name,
            description=project.description,
            asset_ids=tuple(asset for asset in project.asset_ids if asset != asset_id),
            folders=project.folders,
            asset_folder_ids={key: value for key, value in project.asset_folder_ids.items() if key != asset_id},
        )
        self._projects[project_id] = updated
        self._record(actor_subject, "remove_project_asset", asset_id)
        return updated

    def set_tags(self, asset_id: str, tag_keys: set[str], *, actor_subject: str) -> AssetRecord | None:
        asset = self._assets.get(asset_id)
        if asset is None or not tag_keys.issubset(self._tags):
            return None
        asset.tags = set(tag_keys)
        self._record(actor_subject, "assign_tags", asset_id)
        return asset

    def set_tags_batch(self, asset_ids: tuple[str, ...], tag_keys: set[str], *, actor_subject: str) -> list[AssetRecord] | None:
        if not tag_keys.issubset(self._tags) or any(asset_id not in self._assets for asset_id in asset_ids):
            return None
        assets = [self._assets[asset_id] for asset_id in asset_ids]
        for asset in assets:
            asset.tags = set(tag_keys)
            self._record(actor_subject, "batch_assign_tags", asset.id)
        return assets

    def set_favorite(self, asset_id: str, favorite: bool, *, actor_subject: str) -> AssetRecord | None:
        asset = self._assets.get(asset_id)
        if asset is None:
            return None
        asset.favorite = favorite
        self._record(actor_subject, "favorite", asset_id)
        return asset

    def archive(self, asset_id: str, *, actor_subject: str) -> AssetRecord | None:
        asset = self._assets.get(asset_id)
        if asset is None or asset.archived:
            return None
        asset.archived = True
        asset.library_key = "archive"
        asset.relative_path = f"models/{asset.relative_path}"
        self._record(actor_subject, "archive", asset_id)
        return asset

    def archive_batch(self, asset_ids: tuple[str, ...], *, actor_subject: str) -> list[AssetRecord] | None:
        if any(asset_id not in self._assets or self._assets[asset_id].archived for asset_id in asset_ids):
            return None
        assets = [self._assets[asset_id] for asset_id in asset_ids]
        for asset in assets:
            asset.archived = True
            asset.library_key = "archive"
            asset.relative_path = f"models/{asset.relative_path}"
            self._record(actor_subject, "archive", asset.id)
        return assets

    def restore(self, asset_id: str, *, actor_subject: str) -> AssetRecord | None:
        asset = self._assets.get(asset_id)
        if asset is None:
            return None
        asset.archived = False
        self._record(actor_subject, "restore", asset_id)
        return asset

    def move(
        self, asset_id: str, *, destination_library_key: str, destination_relative_path: str, actor_subject: str
    ) -> AssetRecord | None:
        asset = self._assets.get(asset_id)
        if asset is None or destination_library_key not in self._libraries:
            return None
        asset.library_key = destination_library_key
        asset.relative_path = destination_relative_path
        self._record(actor_subject, "move", asset_id)
        return asset

    def permanently_delete(self, asset_id: str, *, actor_subject: str) -> bool:
        if asset_id not in self._assets:
            return False
        del self._assets[asset_id]
        self._record(actor_subject, "permanent_delete", asset_id)
        return True

    def upload(
        self, library_key: str, filename: str, stream: BinaryIO, *, actor_subject: str, collision_policy: Literal["reject", "overwrite", "rename"] = "reject"
    ) -> AssetRecord:
        normalized = _safe_upload_filename(filename)
        if library_key not in self._libraries:
            raise ValueError("unknown library")
        existing = next((asset for asset in self._assets.values() if asset.library_key == library_key and asset.relative_path == normalized), None)
        if existing is not None:
            if collision_policy == "reject":
                raise FileExistsError("destination already exists")
            if collision_policy == "rename":
                stem, suffix = normalized.rsplit(".", 1)
                number = 1
                while any(asset.library_key == library_key and asset.relative_path == f"{stem} ({number}).{suffix}" for asset in self._assets.values()):
                    number += 1
                normalized = f"{stem} ({number}).{suffix}"
            else:
                content = stream.read()
                existing.format = normalized.rsplit(".", 1)[-1].casefold()
                existing.byte_size = len(content)
                existing.content = content
                self._record(actor_subject, "overwrite_upload", existing.id)
                return existing
        extension = normalized.rsplit(".", 1)[-1].casefold()
        if extension not in {"stl", "obj", "3mf"}:
            raise ValueError("unsupported model format")
        content = stream.read()
        asset = AssetRecord(
            id=f"upload-{len(self._assets) + 1}", library_key=library_key, relative_path=normalized, format=extension,
            byte_size=len(content), content=content,
        )
        self._assets[asset.id] = asset
        self._record(actor_subject, "upload", asset.id)
        return asset

    def upload_thumbnail(
        self, asset_id: str, stream: BinaryIO, content_type: str | None, *, actor_subject: str
    ) -> AssetRecord | None:
        asset = self._assets.get(asset_id)
        if asset is None:
            return None
        payload, media_type = ThumbnailCache.read_manual_upload(stream, content_type)
        asset.manual_thumbnail_content = payload
        asset.manual_thumbnail_content_type = media_type
        self._record(actor_subject, "upload_thumbnail", asset_id)
        return asset

    def issue_pairing_code(self, *, actor_subject: str, now: datetime) -> PairingCodeRecord:
        code = f"PAIR-{len(self._helper_pairing_codes) + 1:06d}"
        expires_at = now + timedelta(minutes=5)
        self._helper_pairing_codes[code] = (actor_subject, expires_at)
        self._record(actor_subject, "issue_helper_pairing_code", None)
        return PairingCodeRecord(code=code, expires_at=expires_at)

    def register_helper_device(
        self, *, pairing_code: str, device_name: str, now: datetime
    ) -> HelperDeviceRecord:
        pairing = self._helper_pairing_codes.pop(pairing_code, None)
        if pairing is None:
            self._record("anonymous", "deny_helper_device_registration", None)
            raise ValueError("pairing code is invalid")
        owner_subject, expires_at = pairing
        if expires_at <= now:
            self._record(owner_subject, "deny_helper_device_registration", None)
            raise ValueError("pairing code has expired")
        device_id = f"device-{len(self._helper_devices) + 1}"
        credential = f"credential-{len(self._helper_devices) + 1}"
        device = HelperDeviceRecord(
            device_id=device_id,
            owner_subject=owner_subject,
            name=device_name.strip(),
            credential=credential,
            created_at=now,
        )
        self._helper_devices[device_id] = device
        self._helper_credentials[credential] = device_id
        self._record(owner_subject, "register_helper_device", None)
        return device

    def list_helper_devices(self, *, actor_subject: str) -> list[HelperDeviceRecord]:
        return [
            HelperDeviceRecord(
                device_id=device.device_id,
                owner_subject=device.owner_subject,
                name=device.name,
                created_at=device.created_at,
            )
            for device in sorted(
                self._helper_devices.values(),
                key=lambda item: ((item.created_at or datetime.min.replace(tzinfo=UTC)), item.device_id),
                reverse=True,
            )
            if device.owner_subject == actor_subject
        ]

    def revoke_helper_device(self, *, actor_subject: str, device_id: str) -> bool:
        device = self._helper_devices.get(device_id)
        if device is None or device.owner_subject != actor_subject:
            return False
        if device.credential is not None:
            self._helper_credentials.pop(device.credential, None)
        del self._helper_devices[device_id]
        self._record(actor_subject, "revoke_helper_device", None)
        return True

    def authenticate_helper_device(self, credential: str) -> HelperDevicePrincipal | None:
        device_id = self._helper_credentials.get(credential)
        if device_id is None:
            return None
        device = self._helper_devices[device_id]
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
        asset = self._assets.get(asset_id)
        device = self._helper_devices.get(device_id)
        if asset is None or device is None or device.owner_subject != actor_subject:
            return None
        expires_at = now + timedelta(seconds=expires_in_seconds)
        request_id = f"request-{len(self._helper_jobs) + 1}"
        record = HelperJobRecord(
            request_id=request_id,
            profile_id=profile_id,
            user_id=actor_subject,
            device_id=device_id,
            asset_id=asset_id,
            asset_name=asset.relative_path.rsplit("/", 1)[-1],
            asset_sha256="0" * 64,
            expires_at=expires_at,
        )
        self._helper_jobs[request_id] = record
        self._record(actor_subject, "create_helper_job", asset_id)
        return record

    def redeem_helper_job(
        self,
        *,
        request_id: str,
        device_id: str,
        user_id: str,
        now: datetime,
        origin: str,
    ) -> HelperJobAccess | None:
        record = self._helper_jobs.get(request_id)
        if record is None or request_id in self._helper_redeemed_jobs:
            self._record(user_id, "deny_helper_job_redeem", None)
            return None
        if record.expires_at <= now or record.device_id != device_id or record.user_id != user_id:
            self._record(user_id, "deny_helper_job_redeem", record.asset_id)
            return None
        self._helper_redeemed_jobs.add(request_id)
        self._record(user_id, "redeem_helper_job", record.asset_id)
        return HelperJobAccess(
            request_id=request_id,
            profile_id=record.profile_id,
            user_id=user_id,
            device_id=device_id,
            asset_url=f"{origin}/api/helper/jobs/{request_id}/asset",
            asset_name=record.asset_name,
            asset_sha256=record.asset_sha256,
            expires_at=record.expires_at,
        )

    def open_helper_job_asset(
        self, *, request_id: str, device_id: str, owner_subject: str, now: datetime
    ) -> DownloadHandle | None:
        record = self._helper_jobs.get(request_id)
        if record is None or request_id not in self._helper_redeemed_jobs:
            return None
        if record.device_id != device_id or record.user_id != owner_subject or record.expires_at <= now:
            return None
        asset = self._assets.get(record.asset_id)
        if asset is None:
            return None
        return DownloadHandle(
            filename=record.asset_name,
            content_type=asset.content_type,
            stream=BytesIO(asset.content),
        )

    def list_audit(self) -> list[AuditRecord]:
        return list(self._audit)

    def open_download(self, asset_id: str) -> DownloadHandle | None:
        asset = self._assets.get(asset_id)
        if asset is None:
            return None
        return DownloadHandle(
            filename=asset.relative_path.rsplit("/", 1)[-1],
            content_type=asset.content_type,
            stream=BytesIO(asset.content),
        )

    def open_thumbnail(self, asset_id: str) -> DownloadHandle | None:
        asset = self._assets.get(asset_id)
        if asset is None or not asset.manual_thumbnail_content:
            return None
        return DownloadHandle(
            filename="thumbnail",
            content_type=asset.manual_thumbnail_content_type,
            stream=BytesIO(asset.manual_thumbnail_content),
        )

    def _record(self, actor_subject: str, action: str, asset_id: str | None) -> None:
        self._audit.append(AuditRecord(actor_subject=actor_subject, action=action, asset_id=asset_id))


SessionResolver = Callable[[Request], ApiSession | None]


@dataclass(frozen=True)
class ApiDependencies:
    repository: AssetRepository
    session_resolver: SessionResolver
    now: Callable[[], datetime] = lambda: datetime.now(UTC)


def _safe_relative_path(value: str) -> str:
    raw = value.strip() if isinstance(value, str) else ""
    windows = PureWindowsPath(raw)
    if (
        not raw
        or "\x00" in raw
        or PurePosixPath(raw).is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
    ):
        raise ValueError("destination_relative_path must be a non-empty relative path")
    parts = raw.replace("\\", "/").split("/")
    if any(part == ".." for part in parts):
        raise ValueError("destination_relative_path must not escape its library")
    normalized = [part for part in parts if part and part != "."]
    if not normalized:
        raise ValueError("destination_relative_path must be a non-empty relative path")
    return "/".join(normalized)


class TagCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=128)


class TagAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag_keys: list[str] = Field(min_length=1, max_length=64)


class BatchTagAssignment(TagAssignment):
    asset_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("asset_ids")
    @classmethod
    def unique_asset_ids(cls, value: list[str]) -> list[str]:
        if any(not asset_id or len(asset_id) > 128 for asset_id in value) or len(set(value)) != len(value):
            raise ValueError("asset ids must be unique non-empty values")
        return value


class BatchArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("asset_ids")
    @classmethod
    def unique_asset_ids(cls, value: list[str]) -> list[str]:
        if any(not asset_id or len(asset_id) > 128 for asset_id in value) or len(set(value)) != len(value):
            raise ValueError("asset ids must be unique non-empty values")
        return value


class AppearancePreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appearance: Literal["dark", "light", "system"]


class ExplorerPreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view: Literal["grid", "list"]
    page_size: Literal[25, 50, 100]


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def normalized_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("project name must not be blank")
        return name


class ProjectFolderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    parent_id: str | None = Field(default=None, max_length=32)

    @field_validator("name")
    @classmethod
    def normalized_name(cls, value: str) -> str:
        name = value.strip()
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            raise ValueError("folder name is invalid")
        return name


class ProjectAssetAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folder_id: str | None = Field(default=None, max_length=32)


class BatchProjectAssetAssignment(ProjectAssetAssignment):
    asset_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("asset_ids")
    @classmethod
    def unique_asset_ids(cls, value: list[str]) -> list[str]:
        if any(not asset_id or len(asset_id) > 128 for asset_id in value) or len(set(value)) != len(value):
            raise ValueError("asset ids must be unique non-empty values")
        return value


class FavoriteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    favorite: bool


class MoveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination_library_key: str = Field(min_length=1, max_length=128)
    destination_relative_path: str = Field(min_length=1, max_length=1024)

    @field_validator("destination_relative_path")
    @classmethod
    def destination_must_be_safe_relative_path(cls, value: str) -> str:
        return _safe_relative_path(value)


class LibraryExcludeRuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(min_length=1, max_length=1024)

    @field_validator("pattern")
    @classmethod
    def pattern_must_be_safe_relative_glob(cls, value: str) -> str:
        return normalize_relative_glob_pattern(value)


class HelperDeviceRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pairing_code: str = Field(min_length=6, max_length=64)
    device_name: str = Field(min_length=1, max_length=255)

    @field_validator("device_name")
    @classmethod
    def normalized_device_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("device name must not be blank")
        return name


class HelperJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1, max_length=64)
    asset_id: str = Field(min_length=1, max_length=128)
    profile_id: str = Field(min_length=1, max_length=128)
    expires_in_seconds: int = Field(ge=1, le=300)


class HelperJobRedeem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=255)


def _safe_upload_filename(value: str | None) -> str:
    normalized = _safe_relative_path(value or "")
    if "/" in normalized:
        raise ValueError("upload filename must not contain a path")
    return normalized


def _asset_payload(asset: AssetRecord) -> dict[str, object]:
    return {
        "id": asset.id,
        "library_key": asset.library_key,
        "relative_path": asset.relative_path,
        "filename": asset.relative_path.rsplit("/", 1)[-1],
        "format": asset.format,
        "favorite": asset.favorite,
        "tags": sorted(asset.tags),
        "archived": asset.archived,
        **({"metadata": asset.metadata} if asset.metadata else {}),
        **({"byte_size": asset.byte_size} if asset.byte_size is not None else {}),
    }


def _project_payload(project: ProjectRecord) -> dict[str, object]:
    return {"id": project.id, "name": project.name, "description": project.description, "asset_ids": list(project.asset_ids), "folders": [{"id": folder.id, "name": folder.name, "parent_id": folder.parent_id} for folder in project.folders], "asset_folder_ids": project.asset_folder_ids}


def _library_exclude_rules_payload(items: list[LibraryExcludeRuleRecord]) -> dict[str, list[dict[str, str]]]:
    return {"items": [{"pattern": item.pattern} for item in items]}


def _not_found() -> None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")


def register_api(app: FastAPI, dependencies: ApiDependencies) -> APIRouter:
    """Register the API onto an application with explicitly supplied dependencies.

    The caller is responsible for adapting its verified BFF cookie to
    :class:`ApiSession`.  The router validates that role and derives all
    capabilities from the server-owned RBAC table; no client request can choose
    a role or a host filesystem path.
    """

    router = APIRouter(prefix="/api", tags=["assets"])

    def reject_host_path_parameters(request: Request) -> None:
        forbidden = {"path", "host_path", "file_path", "source_path", "destination_path"}
        if any(key.casefold() in forbidden for key in request.query_params):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="host paths are not accepted")

    def current_actor(request: Request) -> ApiActor:
        session = dependencies.session_resolver(request)
        if session is None or not isinstance(session.subject, str) or not session.subject:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication is required")
        if session.role is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PrintVault access is not granted")
        capabilities = ROLE_CAPABILITIES.get(session.role)
        if capabilities is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication is required")
        return ApiActor(subject=session.subject, role=session.role, capabilities=capabilities)

    def current_helper_device(request: Request) -> HelperDevicePrincipal:
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="device authentication is required")
        device = dependencies.repository.authenticate_helper_device(token)
        if device is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="device authentication is required")
        reject_host_path_parameters(request)
        return device

    def require(capability: str):
        def dependency(request: Request, actor: ApiActor = Depends(current_actor)) -> ApiActor:
            if capability not in actor.capabilities:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient capability")
            reject_host_path_parameters(request)
            return actor

        return dependency

    @router.get("/preferences/appearance")
    def get_appearance_preference(actor: ApiActor = Depends(require("browse"))) -> dict[str, str]:
        return {"appearance": dependencies.repository.get_appearance_preference(actor.subject) or "dark"}

    @router.put("/preferences/appearance")
    def set_appearance_preference(
        payload: AppearancePreferenceUpdate, actor: ApiActor = Depends(require("browse"))
    ) -> dict[str, str]:
        appearance = dependencies.repository.set_appearance_preference(actor.subject, payload.appearance)
        return {"appearance": appearance}

    @router.get("/preferences/explorer")
    def get_explorer_preference(actor: ApiActor = Depends(require("browse"))) -> dict[str, str | int]:
        view, page_size = dependencies.repository.get_explorer_preference(actor.subject) or ("grid", 50)
        return {"view": view, "page_size": page_size}

    @router.put("/preferences/explorer")
    def set_explorer_preference(
        payload: ExplorerPreferenceUpdate, actor: ApiActor = Depends(require("browse"))
    ) -> dict[str, str | int]:
        view, page_size = dependencies.repository.set_explorer_preference(actor.subject, payload.view, payload.page_size)
        return {"view": view, "page_size": page_size}

    @router.get("/projects")
    def projects(_: ApiActor = Depends(require("browse"))) -> dict[str, list[dict[str, object]]]:
        return {"items": [_project_payload(project) for project in dependencies.repository.list_projects()]}

    @router.post("/projects", status_code=status.HTTP_201_CREATED)
    def create_project(payload: ProjectCreate, actor: ApiActor = Depends(require("project"))) -> dict[str, object]:
        try:
            project = dependencies.repository.create_project(payload.name, payload.description.strip(), actor_subject=actor.subject)
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid project") from error
        return _project_payload(project)

    @router.post("/projects/{project_id}/folders", status_code=status.HTTP_201_CREATED)
    def create_project_folder(project_id: str, payload: ProjectFolderCreate, actor: ApiActor = Depends(require("project"))) -> dict[str, object]:
        try:
            folder = dependencies.repository.create_project_folder(project_id, payload.name, payload.parent_id, actor_subject=actor.subject)
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid project folder") from error
        if folder is None:
            _not_found()
        return {"id": folder.id, "name": folder.name, "parent_id": folder.parent_id}

    @router.put("/projects/{project_id}/assets/batch")
    def assign_project_assets_batch(project_id: str, payload: BatchProjectAssetAssignment, actor: ApiActor = Depends(require("project"))) -> dict[str, object]:
        project = dependencies.repository.assign_project_assets_batch(project_id, tuple(payload.asset_ids), folder_id=payload.folder_id, actor_subject=actor.subject)
        if project is None:
            _not_found()
            raise AssertionError("not found always raises")
        return _project_payload(project)

    @router.put("/projects/{project_id}/assets/{asset_id}")
    def assign_project_asset(project_id: str, asset_id: str, payload: ProjectAssetAssignment = ProjectAssetAssignment(), actor: ApiActor = Depends(require("project"))) -> dict[str, object]:
        project = dependencies.repository.assign_project_asset(project_id, asset_id, folder_id=payload.folder_id, actor_subject=actor.subject)
        if project is None:
            _not_found()
        return _project_payload(project)

    @router.delete("/projects/{project_id}/assets/{asset_id}")
    def remove_project_asset(project_id: str, asset_id: str, actor: ApiActor = Depends(require("project"))) -> dict[str, object]:
        project = dependencies.repository.remove_project_asset(project_id, asset_id, actor_subject=actor.subject)
        if project is None:
            _not_found()
        return _project_payload(project)

    @router.get("/libraries")
    def libraries(_: ApiActor = Depends(require("browse"))) -> dict[str, list[dict[str, str]]]:
        return {"items": [{"key": library.key, "name": library.name} for library in dependencies.repository.list_libraries()]}

    @router.get("/admin/libraries/{library_key}/exclude-rules")
    def list_library_exclude_rules(
        library_key: str, _: ApiActor = Depends(require("library_config"))
    ) -> dict[str, list[dict[str, str]]]:
        rules = dependencies.repository.list_library_exclude_rules(library_key)
        if rules is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="library not found")
        return _library_exclude_rules_payload(rules)

    @router.post("/admin/libraries/{library_key}/exclude-rules", status_code=status.HTTP_201_CREATED)
    def add_library_exclude_rule(
        library_key: str, payload: LibraryExcludeRuleUpdate, actor: ApiActor = Depends(require("library_config"))
    ) -> dict[str, list[dict[str, str]]]:
        try:
            rules = dependencies.repository.add_library_exclude_rule(
                library_key, payload.pattern, actor_subject=actor.subject
            )
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
        if rules is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="library not found")
        return _library_exclude_rules_payload(rules)

    @router.delete("/admin/libraries/{library_key}/exclude-rules")
    def remove_library_exclude_rule(
        library_key: str, payload: LibraryExcludeRuleUpdate, actor: ApiActor = Depends(require("library_config"))
    ) -> dict[str, list[dict[str, str]]]:
        try:
            rules = dependencies.repository.remove_library_exclude_rule(
                library_key, payload.pattern, actor_subject=actor.subject
            )
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
        if rules is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="library or rule not found")
        return _library_exclude_rules_payload(rules)

    @router.post("/helper/pairing-codes", status_code=status.HTTP_201_CREATED)
    def issue_helper_pairing_code(actor: ApiActor = Depends(require("download"))) -> dict[str, str]:
        pairing = dependencies.repository.issue_pairing_code(actor_subject=actor.subject, now=dependencies.now())
        return {"pairing_code": pairing.code, "expires_at": pairing.expires_at.isoformat()}

    @router.post("/helper/devices/register", status_code=status.HTTP_201_CREATED)
    def register_helper_device(payload: HelperDeviceRegistration) -> dict[str, str]:
        try:
            device = dependencies.repository.register_helper_device(
                pairing_code=payload.pairing_code, device_name=payload.device_name, now=dependencies.now()
            )
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
        assert device.credential is not None
        return {
            "user_id": device.owner_subject,
            "device_id": device.device_id,
            "device_credential": device.credential,
        }

    @router.get("/helper/devices")
    def list_helper_devices(actor: ApiActor = Depends(require("download"))) -> dict[str, list[dict[str, str]]]:
        devices = dependencies.repository.list_helper_devices(actor_subject=actor.subject)
        return {
            "items": [
                {
                    "device_id": device.device_id,
                    "name": device.name,
                    **({"created_at": device.created_at.isoformat()} if device.created_at is not None else {}),
                }
                for device in devices
            ]
        }

    @router.delete("/helper/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
    def revoke_helper_device(device_id: str, actor: ApiActor = Depends(require("download"))) -> None:
        revoked = dependencies.repository.revoke_helper_device(actor_subject=actor.subject, device_id=device_id)
        if not revoked:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="helper device not found")

    @router.post("/helper/jobs", status_code=status.HTTP_201_CREATED)
    def create_helper_job(payload: HelperJobCreate, request: Request, actor: ApiActor = Depends(require("download"))) -> dict[str, str]:
        job = dependencies.repository.create_helper_job(
            actor_subject=actor.subject,
            device_id=payload.device_id,
            asset_id=payload.asset_id,
            profile_id=payload.profile_id,
            expires_in_seconds=payload.expires_in_seconds,
            now=dependencies.now(),
        )
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset or device not found")
        return {
            "request_id": job.request_id,
            "launch_uri": f"printvault://open?request={job.request_id}&profile={job.profile_id}",
            "expires_at": job.expires_at.isoformat(),
            "device_id": job.device_id,
            "asset_id": job.asset_id,
            "profile_id": job.profile_id,
        }

    @router.post("/helper/jobs/redeem")
    def redeem_helper_job(
        payload: HelperJobRedeem,
        request: Request,
        device: HelperDevicePrincipal = Depends(current_helper_device),
    ) -> dict[str, str]:
        if payload.device_id != device.device_id or payload.user_id != device.owner_subject:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="job is not bound to this device")
        access = dependencies.repository.redeem_helper_job(
            request_id=payload.request_id,
            device_id=payload.device_id,
            user_id=payload.user_id,
            now=dependencies.now(),
            origin=str(request.base_url).rstrip("/"),
        )
        if access is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="helper job redeem was denied")
        return {
            "request_id": access.request_id,
            "profile_id": access.profile_id,
            "user_id": access.user_id,
            "device_id": access.device_id,
            "asset_url": access.asset_url,
            "asset_name": access.asset_name,
            "asset_sha256": access.asset_sha256,
            "expires_at": access.expires_at.isoformat(),
        }

    @router.get("/helper/jobs/{request_id}/asset")
    def download_helper_job_asset(
        request_id: str,
        device: HelperDevicePrincipal = Depends(current_helper_device),
    ) -> StreamingResponse:
        handle = dependencies.repository.open_helper_job_asset(
            request_id=request_id,
            device_id=device.device_id,
            owner_subject=device.owner_subject,
            now=dependencies.now(),
        )
        if handle is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="helper job asset not found")
        return StreamingResponse(
            handle.stream,
            media_type=handle.content_type,
            headers={"Content-Disposition": f"attachment; filename=\"{quote(handle.filename)}\""},
        )

    @router.get("/assets")
    def assets(
        _: ApiActor = Depends(require("browse")),
        q: str | None = None,
        favorite: bool | None = None,
        library: str | None = None,
        tag: str | None = None,
        format: str | None = None,
        project_id: str | None = None,
        folder_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, object]:
        if not 1 <= limit <= 100 or offset < 0:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid pagination")
        try:
            page = dependencies.repository.list_asset_page(
                AssetQuery(
                    q=q,
                    favorite=favorite,
                    library=library,
                    tag=tag,
                    format=format,
                    project_id=project_id,
                    folder_id=folder_id,
                ),
                limit=limit,
                offset=offset,
            )
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid project scope") from error
        return {"items": [_asset_payload(asset) for asset in page.items], "total": page.total, "limit": limit, "offset": offset}

    @router.get("/assets/summary")
    def asset_summary(
        _: ApiActor = Depends(require("browse")),
        library: str | None = None,
        project_id: str | None = None,
        folder_id: str | None = None,
    ) -> dict[str, object]:
        try:
            return dependencies.repository.asset_summary(AssetQuery(library=library, project_id=project_id, folder_id=folder_id))
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid project scope") from error

    @router.post("/uploads")
    def upload_files(
        library_key: str = Form(..., min_length=1, max_length=128),
        collision_policy: Literal["reject", "overwrite", "rename"] = Form("reject"),
        files: list[UploadFile] = File(...),
        actor: ApiActor = Depends(require("upload")),
    ) -> dict[str, object]:
        if library_key == "archive" or library_key not in {library.key for library in dependencies.repository.list_libraries()}:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="uploads require a writable library")
        items: list[dict[str, object]] = []
        rejected: list[dict[str, str]] = []
        for uploaded in files:
            try:
                filename = _safe_upload_filename(uploaded.filename)
                if filename.rsplit(".", 1)[-1].casefold() not in SUPPORTED_FORMATS:
                    raise ValueError("unsupported_format")
                asset = dependencies.repository.upload(
                    library_key, filename, uploaded.file, actor_subject=actor.subject, collision_policy=collision_policy
                )
            except FileExistsError:
                rejected.append({"filename": uploaded.filename or "", "reason": "collision"})
            except ValueError as error:
                reason = "unsupported_format" if str(error) == "unsupported_format" else "invalid_file"
                rejected.append({"filename": uploaded.filename or "", "reason": reason})
            else:
                items.append(_asset_payload(asset))
            finally:
                uploaded.file.close()
        return {"items": items, "rejected": rejected}

    @router.post("/assets/batch/tags")
    def assign_tags_batch(body: BatchTagAssignment, actor: ApiActor = Depends(require("tag"))) -> dict[str, list[dict[str, object]]]:
        assets = dependencies.repository.set_tags_batch(tuple(body.asset_ids), set(body.tag_keys), actor_subject=actor.subject)
        if assets is None:
            _not_found()
            raise AssertionError("not found always raises")
        return {"items": [_asset_payload(asset) for asset in assets]}

    @router.get("/assets/{asset_id}")
    def asset_detail(asset_id: str, _: ApiActor = Depends(require("view"))) -> dict[str, object]:
        asset = dependencies.repository.get_asset(asset_id)
        if asset is None:
            _not_found()
        return _asset_payload(asset)

    @router.get("/assets/{asset_id}/download")
    def download(asset_id: str, _: ApiActor = Depends(require("download"))) -> StreamingResponse:
        handle = dependencies.repository.open_download(asset_id)
        if handle is None:
            _not_found()
        return StreamingResponse(
            handle.stream,
            media_type=handle.content_type,
            headers={"Content-Disposition": f"attachment; filename=\"{quote(handle.filename)}\""},
        )

    @router.get("/assets/{asset_id}/thumbnail")
    def thumbnail(asset_id: str, _: ApiActor = Depends(require("view"))) -> StreamingResponse:
        handle = dependencies.repository.open_thumbnail(asset_id)
        if handle is None:
            _not_found()
        return StreamingResponse(handle.stream, media_type=handle.content_type, headers={"Cache-Control": "private, no-cache"})

    @router.post("/assets/{asset_id}/thumbnail")
    def upload_thumbnail(
        asset_id: str,
        image: UploadFile = File(...),
        actor: ApiActor = Depends(require("upload")),
    ) -> dict[str, object]:
        try:
            asset = dependencies.repository.upload_thumbnail(
                asset_id, image.file, image.content_type, actor_subject=actor.subject
            )
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid thumbnail image") from error
        finally:
            image.file.close()
        if asset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
        return _asset_payload(asset)

    @router.get("/tags")
    def tags(_: ApiActor = Depends(require("browse"))) -> dict[str, list[dict[str, str]]]:
        return {"items": [{"key": tag.key, "name": tag.name} for tag in dependencies.repository.list_tags()]}

    @router.post("/tags", status_code=status.HTTP_201_CREATED)
    def create_tag(body: TagCreate, actor: ApiActor = Depends(require("tag"))) -> dict[str, str]:
        try:
            tag = dependencies.repository.create_tag(body.key, body.name.strip(), actor_subject=actor.subject)
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid tag") from error
        return {"key": tag.key, "name": tag.name}

    @router.put("/assets/{asset_id}/tags")
    def assign_tags(asset_id: str, body: TagAssignment, actor: ApiActor = Depends(require("tag"))) -> dict[str, object]:
        asset = dependencies.repository.set_tags(asset_id, set(body.tag_keys), actor_subject=actor.subject)
        if asset is None:
            _not_found()
        return _asset_payload(asset)

    @router.put("/assets/{asset_id}/favorite")
    def set_favorite(asset_id: str, body: FavoriteUpdate, actor: ApiActor = Depends(require("favorite"))) -> dict[str, object]:
        asset = dependencies.repository.set_favorite(asset_id, body.favorite, actor_subject=actor.subject)
        if asset is None:
            _not_found()
        return _asset_payload(asset)

    @router.post("/assets/batch/archive")
    def archive_batch(body: BatchArchiveRequest, actor: ApiActor = Depends(require("archive"))) -> dict[str, list[dict[str, object]]]:
        try:
            assets = dependencies.repository.archive_batch(tuple(body.asset_ids), actor_subject=actor.subject)
        except (PathCollisionError, UnsafePathError) as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="archive destination is unavailable") from error
        if assets is None:
            _not_found()
            raise AssertionError("not found always raises")
        return {"items": [_asset_payload(asset) for asset in assets]}

    @router.post("/assets/{asset_id}/archive")
    def archive(asset_id: str, actor: ApiActor = Depends(require("archive"))) -> dict[str, object]:
        asset = dependencies.repository.archive(asset_id, actor_subject=actor.subject)
        if asset is None:
            _not_found()
        return _asset_payload(asset)

    @router.post("/assets/{asset_id}/restore")
    def restore(asset_id: str, actor: ApiActor = Depends(require("archive"))) -> dict[str, object]:
        asset = dependencies.repository.restore(asset_id, actor_subject=actor.subject)
        if asset is None:
            _not_found()
        return _asset_payload(asset)

    @router.post("/assets/{asset_id}/move")
    def move(asset_id: str, body: MoveRequest, actor: ApiActor = Depends(require("move"))) -> dict[str, object]:
        asset = dependencies.repository.move(
            asset_id,
            destination_library_key=body.destination_library_key,
            destination_relative_path=body.destination_relative_path,
            actor_subject=actor.subject,
        )
        if asset is None:
            _not_found()
        return _asset_payload(asset)

    @router.delete("/assets/{asset_id}")
    def permanently_delete(asset_id: str, actor: ApiActor = Depends(require("permanent_delete"))) -> dict[str, str]:
        if not dependencies.repository.permanently_delete(asset_id, actor_subject=actor.subject):
            _not_found()
        return {"status": "deleted", "asset_id": asset_id}

    @router.get("/audit")
    def audit(_: ApiActor = Depends(require("audit_access"))) -> dict[str, list[dict[str, str | None]]]:
        return {
            "items": [
                {"actor_subject": event.actor_subject, "action": event.action, "asset_id": event.asset_id}
                for event in dependencies.repository.list_audit()
            ]
        }

    app.include_router(router)
    return router


__all__ = [
    "ApiDependencies",
    "ApiSession",
    "AssetQuery",
    "AssetRecord",
    "AssetRepository",
    "AuditRecord",
    "DownloadHandle",
    "InMemoryAssetRepository",
    "LibraryExcludeRuleRecord",
    "LibraryRecord",
    "TagRecord",
    "register_api",
]
