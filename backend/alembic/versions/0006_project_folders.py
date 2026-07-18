"""Add logical nested project folders.

Revision ID: 0006_project_folders
Revises: 0005_manual_thumbnail
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006_project_folders"
down_revision = "0005_manual_thumbnail"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_folders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("project_folders.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("name_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("project_id", "parent_id", "name_key", name="uq_project_folder_sibling_name"),
    )
    op.create_index("ix_project_folders_project_id", "project_folders", ["project_id"])
    op.create_index("ix_project_folders_parent_id", "project_folders", ["parent_id"])
    with op.batch_alter_table("project_assets") as batch:
        batch.add_column(sa.Column("folder_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_project_assets_folder_id", "project_folders", ["folder_id"], ["id"], ondelete="RESTRICT")
        batch.create_index("ix_project_assets_folder_id", ["folder_id"])


def downgrade() -> None:
    with op.batch_alter_table("project_assets") as batch:
        batch.drop_index("ix_project_assets_folder_id")
        batch.drop_constraint("fk_project_assets_folder_id", type_="foreignkey")
        batch.drop_column("folder_id")
    op.drop_index("ix_project_folders_parent_id", table_name="project_folders")
    op.drop_index("ix_project_folders_project_id", table_name="project_folders")
    op.drop_table("project_folders")
