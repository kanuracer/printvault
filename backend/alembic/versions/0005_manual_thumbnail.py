"""Persist server-owned manual thumbnail content digests for assets.

Revision ID: 0005_manual_thumbnail
Revises: 0004_asset_metadata
Create Date: 2026-07-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_manual_thumbnail"
down_revision = "0004_asset_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("manual_thumbnail_sha", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("assets", "manual_thumbnail_sha")