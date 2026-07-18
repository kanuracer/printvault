"""Role and capability resolution for OIDC group claims."""

from __future__ import annotations

from collections.abc import Iterable

VIEWER_CAPABILITIES = frozenset({"browse", "download", "view"})
EDITOR_CAPABILITIES = VIEWER_CAPABILITIES | frozenset({"tag", "favorite", "move", "archive", "scan", "upload"})
ADMIN_CAPABILITIES = EDITOR_CAPABILITIES | frozenset(
    {"permanent_delete", "library_config", "audit_access"}
)

GROUP_ROLES = {
    "printvault_viewer": "viewer",
    "printvault_editor": "editor",
    "printvault_admin": "admin",
}
ROLE_CAPABILITIES = {
    "viewer": VIEWER_CAPABILITIES,
    "editor": EDITOR_CAPABILITIES,
    "admin": ADMIN_CAPABILITIES,
}
_ROLE_RANK = {"viewer": 1, "editor": 2, "admin": 3}


def role_for_groups(groups: Iterable[str] | None) -> str | None:
    """Resolve the highest PrintVault role from exact OIDC group names."""
    if groups is None:
        return None
    matching_roles = (GROUP_ROLES[group] for group in groups if group in GROUP_ROLES)
    return max(matching_roles, key=_ROLE_RANK.__getitem__, default=None)


def capabilities_for_groups(groups: Iterable[str] | None) -> frozenset[str]:
    """Return capabilities granted by the mapped role, denying unmapped users."""
    role = role_for_groups(groups)
    return ROLE_CAPABILITIES[role] if role is not None else frozenset()


def has_capability(groups: Iterable[str] | None, capability: str) -> bool:
    """Return whether groups grant one exact capability."""
    return capability in capabilities_for_groups(groups)
