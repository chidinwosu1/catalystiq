"""add ml_model_artifact registry table

Revision ID: f9a2c1d4e8b7
Revises: e7b41f9c02da
Create Date: 2026-07-19 22:00:00.000000

Adds the ML model-artifact registry (ML foundation). One row per candidate or
approved artifact, carrying full training/validation/calibration/holdout
window boundaries, schema/target/data versions, code commit, hyperparameters
and evaluation + calibration metrics. Only rows with
approval_status='approved' (and is_synthetic=false) may serve user-facing
predictions - the inference layer enforces that. The whole ML subsystem is
disabled by default; this migration only creates storage.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f9a2c1d4e8b7"
down_revision: Union[str, Sequence[str], None] = "e7b41f9c02da"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ml_model_artifact",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("model_version", sa.String(length=40), nullable=False),
        sa.Column("model_family", sa.String(length=30), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("trade_direction", sa.String(length=10), nullable=False),
        sa.Column("training_start", sa.DateTime(), nullable=True),
        sa.Column("training_end", sa.DateTime(), nullable=True),
        sa.Column("validation_start", sa.DateTime(), nullable=True),
        sa.Column("validation_end", sa.DateTime(), nullable=True),
        sa.Column("calibration_start", sa.DateTime(), nullable=True),
        sa.Column("calibration_end", sa.DateTime(), nullable=True),
        sa.Column("holdout_start", sa.DateTime(), nullable=True),
        sa.Column("holdout_end", sa.DateTime(), nullable=True),
        sa.Column("feature_schema_version", sa.String(length=40), nullable=False),
        sa.Column("target_definition_version", sa.String(length=40), nullable=False),
        sa.Column("training_data_version", sa.String(length=80), nullable=False),
        sa.Column("code_commit", sa.String(length=80), nullable=True),
        sa.Column("hyperparameters", sa.JSON(), nullable=True),
        sa.Column("evaluation_metrics", sa.JSON(), nullable=True),
        sa.Column("calibration_metrics", sa.JSON(), nullable=True),
        sa.Column("approval_status", sa.String(length=20), nullable=False, server_default="candidate"),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("artifact_uri", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("model_name", "model_version", name="uq_ml_model_name_version"),
    )
    op.create_index("ix_ml_model_artifact_model_name", "ml_model_artifact", ["model_name"])
    op.create_index("ix_ml_model_artifact_model_family", "ml_model_artifact", ["model_family"])
    op.create_index("ix_ml_model_artifact_horizon_days", "ml_model_artifact", ["horizon_days"])
    op.create_index("ix_ml_model_artifact_trade_direction", "ml_model_artifact", ["trade_direction"])
    op.create_index("ix_ml_model_artifact_approval_status", "ml_model_artifact", ["approval_status"])


def downgrade() -> None:
    op.drop_index("ix_ml_model_artifact_approval_status", table_name="ml_model_artifact")
    op.drop_index("ix_ml_model_artifact_trade_direction", table_name="ml_model_artifact")
    op.drop_index("ix_ml_model_artifact_horizon_days", table_name="ml_model_artifact")
    op.drop_index("ix_ml_model_artifact_model_family", table_name="ml_model_artifact")
    op.drop_index("ix_ml_model_artifact_model_name", table_name="ml_model_artifact")
    op.drop_table("ml_model_artifact")
