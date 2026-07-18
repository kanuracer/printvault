from __future__ import annotations


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
    assert libraries.json() == {"items": [{"key": "models", "name": "Models"}, {"key": "projects", "name": "Projects"}]}
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
            json={"destination_library_key": "projects", "destination_relative_path": "saved/Bracket.stl"},
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
    assert move.json()["library_key"] == "projects"
    assert move.json()["relative_path"] == "saved/Bracket.stl"
