from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("role", "method", "path", "body", "expected"),
    [
        ("viewer", "get", "/api/libraries", None, 200),
        ("viewer", "get", "/api/assets/asset-bracket", None, 200),
        ("viewer", "get", "/api/assets/asset-bracket/download", None, 200),
        ("viewer", "put", "/api/assets/asset-bracket/favorite", {"favorite": True}, 403),
        ("viewer", "put", "/api/assets/asset-bracket/tags", {"tag_keys": ["art"]}, 403),
        ("viewer", "post", "/api/assets/asset-bracket/archive", None, 403),
        ("editor", "put", "/api/assets/asset-bracket/favorite", {"favorite": True}, 200),
        ("editor", "put", "/api/assets/asset-bracket/tags", {"tag_keys": ["art"]}, 200),
        ("editor", "post", "/api/assets/asset-bracket/archive", None, 200),
        ("editor", "delete", "/api/assets/asset-bracket", None, 403),
        ("editor", "get", "/api/audit", None, 403),
        ("admin", "delete", "/api/assets/asset-bracket", None, 200),
        ("admin", "get", "/api/audit", None, 200),
    ],
)
def test_role_capability_matrix_is_server_enforced(authenticated_client, role, method, path, body, expected) -> None:
    with authenticated_client(role) as client:
        response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)

    assert response.status_code == expected


def test_valid_bff_session_without_printvault_role_is_forbidden(authenticated_client) -> None:
    with authenticated_client("denied") as client:
        response = client.get("/api/assets")

    assert response.status_code == 403


def test_permanent_delete_is_admin_only_and_audit_is_actor_attributed(authenticated_client) -> None:
    with authenticated_client("editor") as client:
        denied = client.delete("/api/assets/asset-cube")

    with authenticated_client("admin") as client:
        archived = client.post("/api/assets/asset-cube/archive")
        deleted = client.delete("/api/assets/asset-cube")
        audit = client.get("/api/audit")

    assert denied.status_code == 403
    assert archived.status_code == 200
    assert deleted.status_code == 200
    assert deleted.json() == {"status": "deleted", "asset_id": "asset-cube"}
    assert audit.status_code == 200
    assert audit.json()["items"][-2:] == [
        {"actor_subject": "admin-subject", "action": "archive", "asset_id": "asset-cube"},
        {"actor_subject": "admin-subject", "action": "permanent_delete", "asset_id": "asset-cube"},
    ]
