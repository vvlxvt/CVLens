"""add cv review history

Revision ID: a3f8d7c9b2e1
Revises: 5ded22309317
Create Date: 2026-07-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f8d7c9b2e1"
down_revision: str | Sequence[str] | None = "5ded22309317"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cv_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uploaded_filename", sa.String(), nullable=True),
        sa.Column("parsed_cv", sa.JSON(), nullable=False),
        sa.Column("similar_examples", sa.JSON(), nullable=False),
        sa.Column("review", sa.JSON(), nullable=False),
        sa.Column("llm", sa.String(), nullable=True),
        sa.Column("user_rating", sa.String(), nullable=True),
        sa.Column("user_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_cv_reviews_created_at"), "cv_reviews", ["created_at"])
    op.create_index(op.f("ix_cv_reviews_llm"), "cv_reviews", ["llm"])
    op.create_index(op.f("ix_cv_reviews_user_rating"), "cv_reviews", ["user_rating"])


def downgrade() -> None:
    op.drop_index(op.f("ix_cv_reviews_user_rating"), table_name="cv_reviews")
    op.drop_index(op.f("ix_cv_reviews_llm"), table_name="cv_reviews")
    op.drop_index(op.f("ix_cv_reviews_created_at"), table_name="cv_reviews")
    op.drop_table("cv_reviews")
