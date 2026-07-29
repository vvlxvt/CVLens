"""add cv review rule sets

Revision ID: b7e4c2d9a1f0
Revises: 907c65a1ef76
Create Date: 2026-07-29 00:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "b7e4c2d9a1f0"
down_revision = "a3f8d7c9b2e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cv_review_rule_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("rules", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("source_review_ids", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version"),
    )
    op.create_index(
        op.f("ix_cv_review_rule_sets_created_at"),
        "cv_review_rule_sets",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cv_review_rule_sets_status"),
        "cv_review_rule_sets",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cv_review_rule_sets_version"),
        "cv_review_rule_sets",
        ["version"],
        unique=True,
    )

    op.create_table(
        "cv_review_rule_diffs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("from_rule_set_id", sa.Integer(), nullable=True),
        sa.Column("to_rule_set_id", sa.Integer(), nullable=False),
        sa.Column("added_rules", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("changed_rules", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("removed_rules", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["from_rule_set_id"], ["cv_review_rule_sets.id"]),
        sa.ForeignKeyConstraint(["to_rule_set_id"], ["cv_review_rule_sets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_cv_review_rule_diffs_created_at"),
        "cv_review_rule_diffs",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cv_review_rule_diffs_to_rule_set_id"),
        "cv_review_rule_diffs",
        ["to_rule_set_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_cv_review_rule_diffs_to_rule_set_id"),
        table_name="cv_review_rule_diffs",
    )
    op.drop_index(
        op.f("ix_cv_review_rule_diffs_created_at"),
        table_name="cv_review_rule_diffs",
    )
    op.drop_table("cv_review_rule_diffs")

    op.drop_index(
        op.f("ix_cv_review_rule_sets_version"),
        table_name="cv_review_rule_sets",
    )
    op.drop_index(
        op.f("ix_cv_review_rule_sets_status"),
        table_name="cv_review_rule_sets",
    )
    op.drop_index(
        op.f("ix_cv_review_rule_sets_created_at"),
        table_name="cv_review_rule_sets",
    )
    op.drop_table("cv_review_rule_sets")
