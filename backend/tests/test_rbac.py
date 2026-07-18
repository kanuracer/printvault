from __future__ import annotations

from app.services.rbac import capabilities_for_groups, has_capability, role_for_groups


def test_exact_viewer_group_gets_browse_download_and_view_capabilities() -> None:
    groups = ["printvault_viewer"]

    assert role_for_groups(groups) == "viewer"
    assert capabilities_for_groups(groups) == frozenset({"browse", "download", "view"})


def test_editor_inherits_viewer_capabilities_and_adds_content_management() -> None:
    capabilities = capabilities_for_groups(["printvault_editor"])

    assert role_for_groups(["printvault_editor"]) == "editor"
    assert {"browse", "download", "view", "tag", "favorite", "move", "archive", "scan"} <= capabilities


def test_admin_inherits_editor_capabilities_and_adds_administration() -> None:
    capabilities = capabilities_for_groups(["printvault_admin"])

    assert role_for_groups(["printvault_admin"]) == "admin"
    assert {
        "browse",
        "download",
        "view",
        "tag",
        "favorite",
        "move",
        "archive",
        "scan",
        "permanent_delete",
        "library_config",
        "audit_access",
    } <= capabilities


def test_multiple_mapped_groups_resolve_to_the_most_privileged_role() -> None:
    groups = ["printvault_viewer", "printvault_editor"]

    assert role_for_groups(groups) == "editor"
    assert has_capability(groups, "archive") is True


def test_unmapped_or_near_match_group_is_denied() -> None:
    groups = ["PRINTVAULT_ADMIN", "printvault_admin_extra", "other"]

    assert role_for_groups(groups) is None
    assert capabilities_for_groups(groups) == frozenset()
    assert has_capability(groups, "browse") is False


def test_missing_group_claim_is_denied() -> None:
    assert role_for_groups(None) is None
    assert capabilities_for_groups(None) == frozenset()
