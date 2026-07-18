from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.filesystem import (
    LibraryRootRegistry,
    PathCollisionError,
    SafeFilesystem,
    UnsafePathError,
)


def library(key: str, root_name: str) -> SimpleNamespace:
    return SimpleNamespace(key=key, root_name=root_name)


def asset(source_library: SimpleNamespace, relative_path: str) -> SimpleNamespace:
    return SimpleNamespace(library=source_library, relative_path=relative_path)


@pytest.fixture
def roots(tmp_path: Path) -> tuple[LibraryRootRegistry, SimpleNamespace, SimpleNamespace, Path, Path]:
    models_root = tmp_path / "models"
    projects_root = tmp_path / "projects"
    models_root.mkdir()
    projects_root.mkdir()
    models = library("models", "Models")
    projects = library("projects", "Projects")
    return (
        LibraryRootRegistry({"Models": models_root, "Projects": projects_root}),
        models,
        projects,
        models_root,
        projects_root,
    )


def test_registry_resolves_an_asset_only_from_its_library_and_relative_path(
    roots: tuple[LibraryRootRegistry, SimpleNamespace, SimpleNamespace, Path, Path],
) -> None:
    registry, models, _, models_root, _ = roots
    target = models_root / "parts" / "bracket.stl"
    target.parent.mkdir()
    target.write_text("mesh", encoding="utf-8")

    resolved = SafeFilesystem(registry).resolve_asset(asset(models, "parts/bracket.stl"), require_regular=True)

    assert resolved == target


@pytest.mark.parametrize("unsafe_path", ["/etc/passwd", "../outside.stl", "parts/../../outside.stl", "C:\\outside.stl", ".private/part.stl"])
def test_resolution_rejects_absolute_traversing_or_hidden_asset_paths(
    roots: tuple[LibraryRootRegistry, SimpleNamespace, SimpleNamespace, Path, Path], unsafe_path: str
) -> None:
    registry, models, _, _, _ = roots

    with pytest.raises(UnsafePathError):
        SafeFilesystem(registry).resolve_asset(asset(models, unsafe_path))


def test_resolution_rejects_a_symlink_final_target_even_when_it_points_inside_the_root(
    roots: tuple[LibraryRootRegistry, SimpleNamespace, SimpleNamespace, Path, Path],
) -> None:
    registry, models, _, models_root, _ = roots
    real_file = models_root / "real.stl"
    real_file.write_text("mesh", encoding="utf-8")
    (models_root / "linked.stl").symlink_to(real_file)

    with pytest.raises(UnsafePathError, match="symlink"):
        SafeFilesystem(registry).resolve_asset(asset(models, "linked.stl"), require_regular=True)


def test_resolution_rejects_a_parent_symlink_that_escapes_the_library(
    roots: tuple[LibraryRootRegistry, SimpleNamespace, SimpleNamespace, Path, Path], tmp_path: Path
) -> None:
    registry, models, _, models_root, _ = roots
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.stl").write_text("not a model", encoding="utf-8")
    (models_root / "linked-directory").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafePathError, match="symlink"):
        SafeFilesystem(registry).resolve_asset(asset(models, "linked-directory/secret.stl"), require_regular=True)


def test_move_between_registered_model_and_project_libraries_preserves_relative_target(
    roots: tuple[LibraryRootRegistry, SimpleNamespace, SimpleNamespace, Path, Path],
) -> None:
    registry, models, projects, models_root, projects_root = roots
    source = models_root / "parts" / "bracket.stl"
    source.parent.mkdir()
    source.write_text("mesh", encoding="utf-8")

    result = SafeFilesystem(registry).move_asset(asset(models, "parts/bracket.stl"), projects, "active/bracket.stl")

    assert result.action == "move"
    assert result.status == "completed"
    assert result.source_library_key == "models"
    assert result.source_relative_path == "parts/bracket.stl"
    assert result.destination_library_key == "projects"
    assert result.destination_relative_path == "active/bracket.stl"
    assert not source.exists()
    assert (projects_root / "active" / "bracket.stl").read_text(encoding="utf-8") == "mesh"


def test_move_rejects_an_existing_destination_without_overwriting_it(
    roots: tuple[LibraryRootRegistry, SimpleNamespace, SimpleNamespace, Path, Path],
) -> None:
    registry, models, projects, models_root, projects_root = roots
    source = models_root / "part.stl"
    destination = projects_root / "part.stl"
    source.write_text("source", encoding="utf-8")
    destination.write_text("destination", encoding="utf-8")

    with pytest.raises(PathCollisionError):
        SafeFilesystem(registry).move_asset(asset(models, "part.stl"), projects, "part.stl")

    assert source.read_text(encoding="utf-8") == "source"
    assert destination.read_text(encoding="utf-8") == "destination"
