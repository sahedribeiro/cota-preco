"""Preserve manual offer reviews separately from source evidence."""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('supplier_offers', sa.Column('review_history_json', sa.JSON(), nullable=False, server_default='[]'))


def downgrade() -> None:
    with op.batch_alter_table('supplier_offers') as batch:
        batch.drop_column('review_history_json')
