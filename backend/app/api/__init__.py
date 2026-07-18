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

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.rbac import ROLE_CAPABILITIES


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
    content: bytes = b""
    content_type: str = "application/octet-stream"


@dataclass(frozen=True)
class TagRecord:
    key: str
    name: str


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

    def set_tags(self, asset_id: str, tag_keys: set[str], *, actor_subject: str) -> AssetRecord | None: ...

    def set_favorite(self, asset_id: str, favorite: bool, *, actor_subject: str) -> AssetRecord | None: ...

    def archive(self, asset_id: str, *, actor_subject: str) -> AssetRecord | None: ...

    def restore(self, asset_id: str, *, actor_subject: str) -> AssetRecord | None: ...

    def move(
        self, asset_id: str, *, destination_library_key: str, destination_relative_path: str, actor_subject: str
    ) -> AssetRecord | None: ...

    def permanently_delete(self, asset_id: str, *, actor_subject: str) -> bool: ...

    def list_audit(self) -> list[AuditRecord]: ...

    def open_download(self, asset_id: str) -> DownloadHandle | None: ...


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
        self._audit: list[AuditRecord] = []

    @classmethod
    def demo(cls) -> InMemoryAssetRepository:
        return cls(
            libraries=(LibraryRecord("models", "Models"), LibraryRecord("projects", "Projects")),
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

    def _record(self, actor_subject: str, action: str, asset_id: str) -> None:
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


class TagAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag_keys: list[str] = Field(min_length=1, max_length=64)


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
    }


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

    @router.get("/tags")
    def tags(_: ApiActor = Depends(require("browse"))) -> dict[str, list[dict[str, str]]]:
        return {"items": [{"key": tag.key, "name": tag.name} for tag in dependencies.repository.list_tags()]}

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
