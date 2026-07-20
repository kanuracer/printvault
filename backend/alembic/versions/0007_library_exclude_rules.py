"""Persist admin-managed per-library exclude rules.

Revision ID: 0007_library_exclude_rules
Revises: 0006_project_folders
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007_library_exclude_rules"
down_revision = "0006_project_folders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "library_exclude_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("library_id", sa.Integer(), sa.ForeignKey("libraries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pattern", sa.String(length=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("library_id", "pattern", name="uq_library_exclude_rules_library_pattern"),
    )
    op.create_index("ix_library_exclude_rules_library_id", "library_exclude_rules", ["library_id"])


def downgrade() -> None:
    op.drop_index("ix_library_exclude_rules_library_id", table_name="library_exclude_rules")
    op.drop_table("library_exclude_rules")
