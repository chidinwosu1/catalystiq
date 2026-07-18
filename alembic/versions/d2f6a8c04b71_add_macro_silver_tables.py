"""add macro Silver tables (series, observation, economic_release)

Revision ID: d2f6a8c04b71
Revises: c1a5b7e93f26
Create Date: 2026-07-18 03:00:00.000000

The macro Silver products (§7, §9, §11): series metadata, point-in-time
observations (unique per vintage so revisions never overwrite the originally-
known value), and economic releases. All carry the SilverRecordMixin common
columns.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d2f6a8c04b71"
down_revision: Union[str, Sequence[str], None] = "c1a5b7e93f26"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _mixin_columns() -> list:
    return [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stable_identifier", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("source_record_id", sa.String(length=255), nullable=True),
        sa.Column("source_available_at", sa.DateTime(), nullable=True),
        sa.Column("effective_at", sa.DateTime(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(), nullable=False),
        sa.Column("bronze_ingestion_run_id", sa.Integer(), nullable=True),
        sa.Column("validation_status", sa.String(length=20), nullable=False),
        sa.Column("data_quality_warnings", sa.JSON(), nullable=True),
        sa.Column("normalization_version", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "silver_macro_series",
        *_mixin_columns(),
        sa.Column("series_id", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("frequency", sa.String(length=30), nullable=True),
        sa.Column("units", sa.String(length=100), nullable=True),
        sa.Column("seasonal_adjustment", sa.String(length=50), nullable=True),
        sa.Column("observation_start", sa.Date(), nullable=True),
        sa.Column("observation_end", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(["bronze_ingestion_run_id"], ["bronze_ingestion_run.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "series_id", name="uq_silver_macro_series"),
    )
    op.create_index(
        op.f("ix_silver_macro_series_stable_identifier"),
        "silver_macro_series", ["stable_identifier"], unique=False,
    )
    op.create_index(
        op.f("ix_silver_macro_series_series_id"), "silver_macro_series", ["series_id"], unique=False
    )

    op.create_table(
        "silver_macro_observation",
        *_mixin_columns(),
        sa.Column("series_id", sa.String(length=50), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("realtime_start", sa.Date(), nullable=True),
        sa.Column("realtime_end", sa.Date(), nullable=True),
        sa.Column("units", sa.String(length=100), nullable=True),
        sa.Column("frequency", sa.String(length=30), nullable=True),
        sa.Column("seasonal_adjustment", sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(["bronze_ingestion_run_id"], ["bronze_ingestion_run.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "series_id", "observation_date", "realtime_start",
            name="uq_silver_macro_observation_vintage",
        ),
    )
    op.create_index(
        op.f("ix_silver_macro_observation_stable_identifier"),
        "silver_macro_observation", ["stable_identifier"], unique=False,
    )
    op.create_index(
        op.f("ix_silver_macro_observation_series_id"),
        "silver_macro_observation", ["series_id"], unique=False,
    )
    op.create_index(
        op.f("ix_silver_macro_observation_observation_date"),
        "silver_macro_observation", ["observation_date"], unique=False,
    )

    op.create_table(
        "silver_economic_release",
        *_mixin_columns(),
        sa.Column("release_id", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=True),
        sa.Column("scheduled_date", sa.Date(), nullable=True),
        sa.Column("actual_published_at", sa.DateTime(), nullable=True),
        sa.Column("press_release", sa.Boolean(), nullable=True),
        sa.Column("link", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["bronze_ingestion_run_id"], ["bronze_ingestion_run.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "release_id", "scheduled_date", name="uq_silver_economic_release"
        ),
    )
    op.create_index(
        op.f("ix_silver_economic_release_stable_identifier"),
        "silver_economic_release", ["stable_identifier"], unique=False,
    )
    op.create_index(
        op.f("ix_silver_economic_release_release_id"),
        "silver_economic_release", ["release_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_silver_economic_release_release_id"), table_name="silver_economic_release")
    op.drop_index(
        op.f("ix_silver_economic_release_stable_identifier"), table_name="silver_economic_release"
    )
    op.drop_table("silver_economic_release")

    op.drop_index(
        op.f("ix_silver_macro_observation_observation_date"), table_name="silver_macro_observation"
    )
    op.drop_index(
        op.f("ix_silver_macro_observation_series_id"), table_name="silver_macro_observation"
    )
    op.drop_index(
        op.f("ix_silver_macro_observation_stable_identifier"), table_name="silver_macro_observation"
    )
    op.drop_table("silver_macro_observation")

    op.drop_index(op.f("ix_silver_macro_series_series_id"), table_name="silver_macro_series")
    op.drop_index(
        op.f("ix_silver_macro_series_stable_identifier"), table_name="silver_macro_series"
    )
    op.drop_table("silver_macro_series")
