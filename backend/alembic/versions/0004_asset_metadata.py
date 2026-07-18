"""Persist bounded extracted 3MF presentation metadata.

Revision ID: 0004_asset_metadata
Revises: 0003_projects
Create Date: 2026-07-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_asset_metadata"
down_revision = "0003_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("assets", "metadata_json")
