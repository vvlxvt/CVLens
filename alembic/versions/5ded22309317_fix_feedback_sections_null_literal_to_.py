"""fix feedback_sections null literal to real NULL

Revision ID: 5ded22309317
Revises: 907c65a1ef76
Create Date: 2026-07-12 13:36:18.136306

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5ded22309317'
down_revision: Union[str, Sequence[str], None] = '907c65a1ef76'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE resumes SET feedback_sections = NULL WHERE feedback_sections = 'null'")

def downgrade() -> None:
    pass  # data migration
