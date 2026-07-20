"""Add helper device pairing and one-time job tables.

Revision ID: 0008_helper_pairing_and_jobs
Revises: 0007_library_exclude_rules
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0008_helper_pairing_and_jobs"
down_revision = "0007_library_exclude_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "helper_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("owner_subject", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("credential_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("device_id"),
        sa.UniqueConstraint("credential_hash"),
    )
    op.create_index("ix_helper_devices_owner_subject", "helper_devices", ["owner_subject"])

    op.create_table(
        "helper_pairing_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_subject", sa.String(length=255), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("code_hash"),
    )
    op.create_index("ix_helper_pairing_codes_owner_subject", "helper_pairing_codes", ["owner_subject"])
    op.create_index("ix_helper_pairing_codes_expires_at", "helper_pairing_codes", ["expires_at"])

    op.create_table(
        "helper_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_subject", sa.String(length=255), nullable=False),
        sa.Column("request_id_hash", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("helper_devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("request_id_hash"),
    )
    op.create_index("ix_helper_jobs_owner_subject", "helper_jobs", ["owner_subject"])
    op.create_index("ix_helper_jobs_device_id", "helper_jobs", ["device_id"])
    op.create_index("ix_helper_jobs_asset_id", "helper_jobs", ["asset_id"])
    op.create_index("ix_helper_jobs_expires_at", "helper_jobs", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_helper_jobs_expires_at", table_name="helper_jobs")
    op.drop_index("ix_helper_jobs_asset_id", table_name="helper_jobs")
    op.drop_index("ix_helper_jobs_device_id", table_name="helper_jobs")
    op.drop_index("ix_helper_jobs_owner_subject", table_name="helper_jobs")
    op.drop_table("helper_jobs")

    op.drop_index("ix_helper_pairing_codes_expires_at", table_name="helper_pairing_codes")
    op.drop_index("ix_helper_pairing_codes_owner_subject", table_name="helper_pairing_codes")
    op.drop_table("helper_pairing_codes")

    op.drop_index("ix_helper_devices_owner_subject", table_name="helper_devices")
    op.drop_table("helper_devices")
