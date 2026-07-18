"""Authenticated, dependency-injected asset API for PrintVault.

This module intentionally has no database or filesystem-global dependency.  The
application composition root supplies a repository and a BFF-session resolver
via :func:`register_api`; production wiring can adapt existing persistence and
archive services without exposing host paths to HTTP callers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import PurePosixPath, PureWindowsPath
from typing import BinaryIO, Protocol
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.rbac import ROLE_CAPABILITIES
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


class AssetRepository(Protocol):
    """Small persistence/service boundary used by the HTTP router."""

    def list_libraries(self) -> list[LibraryRecord]: ...

    def list_assets(self, query: AssetQuery) -> list[AssetRecord]: ...

    def get_asset(self, asset_id: str) -> AssetRecord | None: ...

    def list_tags(self) -> list[TagRecord]: ...

    def create_tag(self, key: str, name: str, *, actor_subject: str) -> TagRecord: ...

    def list_projects(self) -> list[ProjectRecord]: ...

    def create_project(self, name: str, description: str, *, actor_subject: str) -> ProjectRecord: ...

    def assign_project_asset(self, project_id: str, asset_id: str, *, folder_id: str | None = None, actor_subject: str) -> ProjectRecord | None: ...

    def remove_project_asset(self, project_id: str, asset_id: str, *, actor_subject: str) -> ProjectRecord | None: ...

    def create_project_folder(self, project_id: str, name: str, parent_id: str | None, *, actor_subject: str) -> ProjectFolderRecord | None: ...

    def set_tags(self, asset_id: str, tag_keys: set[str], *, actor_subject: str) -> AssetRecord | None: ...

    def set_favorite(self, asset_id: str, favorite: bool, *, actor_subject: str) -> AssetRecord | None: ...

    def archive(self, asset_id: str, *, actor_subject: str) -> AssetRecord | None: ...

    def restore(self, asset_id: str, *, actor_subject: str) -> AssetRecord | None: ...

    def move(
        self, asset_id: str, *, destination_library_key: str, destination_relative_path: str, actor_subject: str
    ) -> AssetRecord | None: ...

    def permanently_delete(self, asset_id: str, *, actor_subject: str) -> bool: ...

    def upload(self, library_key: str, filename: str, stream: BinaryIO, *, actor_subject: str) -> AssetRecord: ...

    def upload_thumbnail(
        self, asset_id: str, stream: BinaryIO, content_type: str | None, *, actor_subject: str
    ) -> AssetRecord | None: ...

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
        return sorted(assets, key=lambda asset: asset.id)

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
        updated = ProjectRecord(id=project.id, name=project.name, description=project.description, asset_ids=tuple(sorted({*project.asset_ids, asset_id})))
        self._projects[project_id] = updated
        self._record(actor_subject, "assign_project_asset", asset_id)
        return updated

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

    def set_favorite(self, asset_id: str, favorite: bool, *, actor_subject: str) -> AssetRecord | None:
        asset = self._assets.get(asset_id)
        if asset is None:
            return None
        asset.favorite = favorite
        self._record(actor_subject, "favorite", asset_id)
        return asset

    def archive(self, asset_id: str, *, actor_subject: str) -> AssetRecord | None:
        asset = self._assets.get(asset_id)
        if asset is None:
            return None
        asset.archived = True
        self._record(actor_subject, "archive", asset_id)
        return asset

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

    def upload(self, library_key: str, filename: str, stream: BinaryIO, *, actor_subject: str) -> AssetRecord:
        normalized = _safe_upload_filename(filename)
        if library_key not in self._libraries:
            raise ValueError("unknown library")
        if any(asset.library_key == library_key and asset.relative_path == normalized for asset in self._assets.values()):
            raise FileExistsError("destination already exists")
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

    def require(capability: str):
        def dependency(request: Request, actor: ApiActor = Depends(current_actor)) -> ApiActor:
            if capability not in actor.capabilities:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient capability")
            reject_host_path_parameters(request)
            return actor

        return dependency

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

    @router.get("/assets")
    def assets(
        _: ApiActor = Depends(require("browse")),
        q: str | None = None,
        favorite: bool | None = None,
        library: str | None = None,
        tag: str | None = None,
        format: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, object]:
        if not 1 <= limit <= 100 or offset < 0:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid pagination")
        matches = dependencies.repository.list_assets(AssetQuery(q=q, favorite=favorite, library=library, tag=tag, format=format))
        return {"items": [_asset_payload(asset) for asset in matches[offset : offset + limit]], "total": len(matches), "limit": limit, "offset": offset}

    @router.post("/uploads")
    def upload_files(
        library_key: str = Form(..., min_length=1, max_length=128),
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
                asset = dependencies.repository.upload(library_key, filename, uploaded.file, actor_subject=actor.subject)
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
    "LibraryRecord",
    "TagRecord",
    "register_api",
]
