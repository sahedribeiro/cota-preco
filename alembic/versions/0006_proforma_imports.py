"""Proforma drafts and idempotent reviewed lines."""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('proforma_imports',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('purchase_request_id', sa.Integer(), sa.ForeignKey('purchase_requests.id'), nullable=False),
        sa.Column('stored_filename', sa.String(80), nullable=False),
        sa.Column('draft_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_proforma_imports_purchase_request_id', 'proforma_imports', ['purchase_request_id'])
    with op.batch_alter_table('supplier_offers') as batch:
        batch.add_column(sa.Column('document_line_key', sa.String(80), nullable=True))
        batch.create_unique_constraint('uq_offer_document_line', ['document_line_key'])


def downgrade() -> None:
    with op.batch_alter_table('supplier_offers') as batch:
        batch.drop_constraint('uq_offer_document_line', type_='unique')
        batch.drop_column('document_line_key')
    op.drop_index('ix_proforma_imports_purchase_request_id', 'proforma_imports')
    op.drop_table('proforma_imports')
