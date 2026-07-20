from __future__ import annotations

from app.api import AssetRecord


def test_asset_endpoints_require_a_server_verified_bff_session(client) -> None:
    response = client.get("/api/assets")

    assert response.status_code == 401


def test_authentication_precedes_host_path_validation(client) -> None:
    response = client.get("/api/assets", params={"path": "/etc/passwd"})

    assert response.status_code == 401


def test_capability_check_precedes_host_path_validation(authenticated_client) -> None:
    with authenticated_client("viewer") as client:
        response = client.delete("/api/assets/asset-bracket", params={"path": "/etc/passwd"})

    assert response.status_code == 403


def test_libraries_and_filtered_paginated_assets_expose_only_safe_metadata(authenticated_client) -> None:
    with authenticated_client("viewer") as client:
        libraries = client.get("/api/libraries")
        response = client.get(
            "/api/assets",
            params={"q": "bracket", "favorite": "true", "library": "models", "tag": "functional", "format": "stl", "limit": 1, "offset": 0},
        )

    assert libraries.status_code == 200
    assert libraries.json() == {"items": [{"key": "archive", "name": "Archive"}, {"key": "models", "name": "Models"}]}
    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": "asset-bracket",
                "library_key": "models",
                "relative_path": "functional/Bracket.stl",
                "filename": "Bracket.stl",
                "format": "stl",
                "favorite": True,
                "tags": ["functional"],
                "archived": False,
            }
        ],
        "total": 1,
        "limit": 1,
        "offset": 0,
    }
    assert "/" not in response.json()["items"][0]["library_key"]
    assert "root" not in response.text.casefold()


def test_asset_detail_tags_favorite_and_download_use_asset_id_only(authenticated_client) -> None:
    with authenticated_client("viewer") as client:
        detail = client.get("/api/assets/asset-bracket")
        tags = client.get("/api/tags")
        download = client.get("/api/assets/asset-bracket/download")

    assert detail.status_code == 200
    assert detail.json()["id"] == "asset-bracket"
    assert tags.json() == {"items": [{"key": "art", "name": "Art"}, {"key": "functional", "name": "Functional"}]}
    assert download.status_code == 200
    assert download.headers["content-disposition"] == 'attachment; filename="Bracket.stl"'
    assert download.headers["content-type"].startswith("model/stl")
    assert download.content == b"solid bracket"


def test_api_rejects_host_path_inputs_and_unsafe_move_destination(authenticated_client) -> None:
    with authenticated_client("editor") as client:
        query_path = client.get("/api/assets/asset-bracket/download", params={"path": "/etc/passwd"})
        absolute_destination = client.post(
            "/api/assets/asset-bracket/move",
            json={"destination_library_key": "projects", "destination_relative_path": "/etc/passwd"},
        )
        windows_destination = client.post(
            "/api/assets/asset-bracket/move",
            json={"destination_library_key": "projects", "destination_relative_path": r"C:\\Windows\\system32"},
        )
        traversal_destination = client.post(
            "/api/assets/asset-bracket/move",
            json={"destination_library_key": "projects", "destination_relative_path": "../escape.stl"},
        )
        unexpected_path_field = client.post(
            "/api/assets/asset-bracket/move",
            json={"destination_library_key": "projects", "destination_relative_path": "saved/Bracket.stl", "host_path": "/etc/passwd"},
        )

    assert query_path.status_code == 422
    assert absolute_destination.status_code == 422
    assert windows_destination.status_code == 422
    assert traversal_destination.status_code == 422
    assert unexpected_path_field.status_code == 422


def test_editor_can_assign_tags_favorite_and_archive_restore_move_without_host_paths(authenticated_client) -> None:
    with authenticated_client("editor") as client:
        assigned = client.put("/api/assets/asset-bracket/tags", json={"tag_keys": ["functional", "art"]})
        favorite = client.put("/api/assets/asset-bracket/favorite", json={"favorite": False})
        archive = client.post("/api/assets/asset-bracket/archive")
        restore = client.post("/api/assets/asset-bracket/restore")
        move = client.post(
            "/api/assets/asset-bracket/move",
            json={"destination_library_key": "archive", "destination_relative_path": "saved/Bracket.stl"},
        )

    assert assigned.status_code == 200
    assert assigned.json()["tags"] == ["art", "functional"]
    assert favorite.status_code == 200
    assert favorite.json()["favorite"] is False
    assert archive.status_code == 200
    assert archive.json()["archived"] is True
    assert restore.status_code == 200
    assert restore.json()["archived"] is False
    assert move.status_code == 200
    assert move.json()["library_key"] == "archive"
    assert move.json()["relative_path"] == "saved/Bracket.stl"


def test_assets_can_be_scoped_to_a_project_folder(authenticated_client) -> None:
    with authenticated_client("editor") as client:
        project = client.post("/api/projects", json={"name": "Werkbank", "description": ""}).json()
        folder = client.post(f"/api/projects/{project['id']}/folders", json={"name": "Druckteile", "parent_id": None}).json()
        assigned = client.put(
            f"/api/projects/{project['id']}/assets/asset-bracket",
            json={"folder_id": folder["id"]},
        )

    with authenticated_client("viewer") as client:
        response = client.get(
            "/api/assets",
            params={"project_id": project["id"], "folder_id": folder["id"], "limit": 50, "offset": 0},
        )

    assert assigned.status_code == 200
    assert response.status_code == 200
    assert [asset["id"] for asset in response.json()["items"]] == ["asset-bracket"]
    assert response.json()["total"] == 1


def test_admin_can_manage_library_exclude_rules_with_validation_and_audit(authenticated_client) -> None:
    with authenticated_client("admin") as client:
        empty = client.get("/api/admin/libraries/models/exclude-rules")
        created = client.post("/api/admin/libraries/models/exclude-rules", json={"pattern": "./drafts/**/*.stl"})
        invalid = client.post("/api/admin/libraries/models/exclude-rules", json={"pattern": "file:///tmp/*.stl"})
        removed = client.request("DELETE", "/api/admin/libraries/models/exclude-rules", json={"pattern": "drafts/**/*.stl"})
        audit = client.get("/api/audit")

    assert empty.status_code == 200
    assert empty.json() == {"items": []}
    assert created.status_code == 201
    assert created.json() == {"items": [{"pattern": "drafts/**/*.stl"}]}
    assert invalid.status_code == 422
    assert removed.status_code == 200
    assert removed.json() == {"items": []}
    assert audit.json()["items"][-2:] == [
        {"actor_subject": "admin-subject", "action": "add_library_exclude_rule", "asset_id": None},
        {"actor_subject": "admin-subject", "action": "remove_library_exclude_rule", "asset_id": None},
    ]


def test_asset_pagination_returns_a_stable_second_page(repository, authenticated_client) -> None:
    for index in range(101):
        repository._assets[f"asset-{index:03d}"] = AssetRecord(
            id=f"asset-{index:03d}",
            library_key="models",
            relative_path=f"bulk/model-{index:03d}.stl",
            format="stl",
        )

    with authenticated_client("viewer") as client:
        response = client.get("/api/assets", params={"limit": 50, "offset": 50})

    assert response.status_code == 200
    assert response.json()["total"] == 103
    assert len(response.json()["items"]) == 50
    assert response.json()["items"][0]["id"] == "asset-050"


def test_asset_summary_uses_the_same_project_scope(authenticated_client) -> None:
    with authenticated_client("editor") as client:
        project = client.post("/api/projects", json={"name": "Workbench"}).json()
        folder = client.post(f"/api/projects/{project['id']}/folders", json={"name": "Parts"}).json()
        client.put(f"/api/projects/{project['id']}/assets/asset-bracket", json={"folder_id": folder["id"]})

        response = client.get(f"/api/assets/summary?project_id={project['id']}&folder_id={folder['id']}")

    assert response.status_code == 200
    assert response.json() == {"total": 1, "size_bytes": 0, "formats": {"stl": 1}}


def test_batch_tag_assignment_is_atomic_and_limited_to_one_hundred_assets(repository, authenticated_client) -> None:
    original_tags = set(repository._assets["asset-bracket"].tags)
    with authenticated_client("editor") as client:
        rejected = client.post("/api/assets/batch/tags", json={"asset_ids": ["asset-bracket", "missing"], "tag_keys": ["art"]})
        assert rejected.status_code == 404
        assert repository._assets["asset-bracket"].tags == original_tags

        accepted = client.post("/api/assets/batch/tags", json={"asset_ids": ["asset-bracket", "asset-cube"], "tag_keys": ["art"]})
        over_limit = client.post("/api/assets/batch/tags", json={"asset_ids": [f"asset-{index}" for index in range(101)], "tag_keys": ["art"]})

    assert repository._assets["asset-bracket"].tags == {"art"}
    assert repository._assets["asset-cube"].tags == {"art"}
    assert accepted.status_code == 200
    assert [item["id"] for item in accepted.json()["items"]] == ["asset-bracket", "asset-cube"]
    assert over_limit.status_code == 422
    assert original_tags == {"functional"}


def test_batch_project_assignment_is_atomic_and_scoped_to_one_project(repository, authenticated_client) -> None:
    with authenticated_client("editor") as client:
        project = client.post("/api/projects", json={"name": "Batch target"}).json()
        rejected = client.put(f"/api/projects/{project['id']}/assets/batch", json={"asset_ids": ["asset-bracket", "missing"]})
        assert rejected.status_code == 404
        assert repository._projects[project["id"]].asset_ids == ()

        accepted = client.put(f"/api/projects/{project['id']}/assets/batch", json={"asset_ids": ["asset-bracket", "asset-cube"]})

    assert accepted.status_code == 200
    assert accepted.json()["asset_ids"] == ["asset-bracket", "asset-cube"]


def test_batch_archive_is_atomic_and_limited_to_one_hundred_assets(repository, authenticated_client) -> None:
    original_bracket = (repository._assets["asset-bracket"].library_key, repository._assets["asset-bracket"].relative_path)
    original_cube = (repository._assets["asset-cube"].library_key, repository._assets["asset-cube"].relative_path)
    with authenticated_client("editor") as client:
        rejected = client.post("/api/assets/batch/archive", json={"asset_ids": ["asset-bracket", "missing"]})
        assert rejected.status_code == 404
        assert (repository._assets["asset-bracket"].library_key, repository._assets["asset-bracket"].relative_path) == original_bracket
        assert (repository._assets["asset-cube"].library_key, repository._assets["asset-cube"].relative_path) == original_cube

        accepted = client.post("/api/assets/batch/archive", json={"asset_ids": ["asset-bracket", "asset-cube"]})
        over_limit = client.post("/api/assets/batch/archive", json={"asset_ids": [f"asset-{index}" for index in range(101)]})

    assert accepted.status_code == 200
    assert [item["id"] for item in accepted.json()["items"]] == ["asset-bracket", "asset-cube"]
    assert all(item["archived"] is True for item in accepted.json()["items"])
    assert repository._assets["asset-bracket"].library_key == "archive"
    assert repository._assets["asset-cube"].library_key == "archive"
    assert over_limit.status_code == 422
