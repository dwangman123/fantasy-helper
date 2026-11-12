"""team owner names

Revision ID: 9b2d4f61c7a3
Revises: 5f1861e17eaa
Create Date: 2026-11-12 01:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9b2d4f61c7a3'
down_revision: Union[str, Sequence[str], None] = '5f1861e17eaa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('teams', schema=None) as batch_op:
        batch_op.add_column(sa.Column('owner', sa.String(length=200), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('teams', schema=None) as batch_op:
        batch_op.drop_column('owner')
