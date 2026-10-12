"""Sales quotation snapshots."""
from alembic import op
import sqlalchemy as sa
revision='0007'
down_revision='0006'
branch_labels=None
depends_on=None


def upgrade() -> None:
    op.create_table('sales_quotes',sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('purchase_request_id',sa.Integer(),sa.ForeignKey('purchase_requests.id'),unique=True,nullable=False),
        sa.Column('company_json',sa.JSON(),nullable=False),sa.Column('customer',sa.Text(),nullable=False),
        sa.Column('terms',sa.Text(),nullable=False),sa.Column('pricing_method',sa.String(20),nullable=False),
        sa.Column('percentage',sa.Numeric(8,3),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('sales_quote_lines',sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('quote_id',sa.Integer(),sa.ForeignKey('sales_quotes.id'),nullable=False),
        sa.Column('item_id',sa.Integer(),sa.ForeignKey('purchase_request_items.id'),nullable=False),
        sa.Column('description',sa.Text(),nullable=False),sa.Column('unit',sa.String(40),nullable=False),
        sa.Column('quantity',sa.Numeric(14,3),nullable=False),sa.Column('currency',sa.String(3),nullable=False),
        sa.Column('cost',sa.Numeric(18,2),nullable=False),sa.Column('sale_price',sa.Numeric(18,2),nullable=False),
        sa.Column('source_json',sa.JSON(),nullable=False))
    op.create_index('ix_sales_quote_lines_quote_id','sales_quote_lines',['quote_id'])


def downgrade() -> None:
    op.drop_index('ix_sales_quote_lines_quote_id','sales_quote_lines')
    op.drop_table('sales_quote_lines')
    op.drop_table('sales_quotes')
