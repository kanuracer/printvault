"""PrintVault persistence models.

Assets deliberately persist only paths relative to a configured library.  The
server's mounted-library configuration is the authority for any host path.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db import Base


def normalize_relative_path(value: str) -> str:
    """Return a stable POSIX-relative asset path or reject an unsafe path."""
    raw_path = value.strip() if isinstance(value, str) else ""
    windows_path = PureWindowsPath(raw_path)
    if not raw_path or PurePosixPath(raw_path).is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise ValueError("asset path must be a non-empty relative path")

    parts = raw_path.replace("\\", "/").split("/")
    if any(part == ".." for part in parts):
        raise ValueError("asset path must be a non-escaping relative path")
    normalized_parts = [part for part in parts if part and part != "."]
    if not normalized_parts:
        raise ValueError("asset path must be a non-empty relative path")
    return "/".join(normalized_parts)


class Library(Base):
    __tablename__ = "libraries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    root_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    assets: Mapped[list["Asset"]] = relationship(back_populates="library")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id", ondelete="RESTRICT"), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manual_thumbnail_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    format: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    library: Mapped[Library] = relationship(back_populates="assets")
    tag_links: Mapped[list["AssetTag"]] = relationship(back_populates="asset", cascade="all, delete-orphan")
    tags: Mapped[list["Tag"]] = relationship(
        secondary="asset_tags", back_populates="assets", overlaps="asset,tag_links,tag"
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="asset")
    project_links: Mapped[list["ProjectAsset"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan", overlaps="projects,assets"
    )
    projects: Mapped[list["Project"]] = relationship(
        secondary="project_assets", back_populates="assets", overlaps="asset,project_links,project"
    )

    @validates("relative_path")
    def validate_relative_path(self, _: str, value: str) -> str:
        return normalize_relative_path(value)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    assets: Mapped[list[Asset]] = relationship(
        secondary="asset_tags", back_populates="tags", overlaps="asset,tag_links,tag"
    )

    @validates("name")
    def normalize_name(self, _: str, value: str) -> str:
        name = value.strip() if isinstance(value, str) else ""
        if not name:
            raise ValueError("tag name must not be empty")
        self.key = name.casefold()
        return name


class AssetTag(Base):
    __tablename__ = "asset_tags"

    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    asset: Mapped[Asset] = relationship(back_populates="tag_links", overlaps="assets,tags")
    tag: Mapped[Tag] = relationship(overlaps="assets,tags")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    asset_links: Mapped[list["ProjectAsset"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", overlaps="assets,projects"
    )
    assets: Mapped[list[Asset]] = relationship(
        secondary="project_assets", back_populates="projects", overlaps="asset,project_links,project,asset_links"
    )
    folders: Mapped[list["ProjectFolder"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class ProjectFolder(Base):
    __tablename__ = "project_folders"
    __table_args__ = (UniqueConstraint("project_id", "parent_id", "name_key", name="uq_project_folder_sibling_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("project_folders.id", ondelete="RESTRICT"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    name_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="folders")
    parent: Mapped["ProjectFolder | None"] = relationship(remote_side="ProjectFolder.id", back_populates="children")
    children: Mapped[list["ProjectFolder"]] = relationship(back_populates="parent")

    @validates("name")
    def normalize_name(self, _: str, value: str) -> str:
        name = value.strip() if isinstance(value, str) else ""
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            raise ValueError("project folder name is invalid")
        self.name_key = name.casefold()
        return name


class ProjectAsset(Base):
    __tablename__ = "project_assets"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    folder_id: Mapped[int | None] = mapped_column(ForeignKey("project_folders.id", ondelete="RESTRICT"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="asset_links", overlaps="assets,projects")
    asset: Mapped[Asset] = relationship(back_populates="project_links", overlaps="assets,projects")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    asset: Mapped[Asset | None] = relationship(back_populates="audit_events")


class UserPreference(Base):
    __tablename__ = "user_preferences"
    __table_args__ = (UniqueConstraint("subject", "key", name="uq_user_preferences_subject_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SlicerProfile(Base):
    __tablename__ = "slicer_profiles"
    __table_args__ = (UniqueConstraint("owner_subject", "name", name="uq_slicer_profiles_owner_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


__all__ = [
    "Asset",
    "AssetTag",
    "AuditEvent",
    "Library",
    "Project",
    "ProjectAsset",
    "SlicerProfile",
    "Tag",
    "UserPreference",
    "normalize_relative_path",
]
