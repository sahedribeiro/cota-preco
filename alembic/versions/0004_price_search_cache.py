"""price_search_cache"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Recover the column left by an interrupted SQLite migration.
    existing = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('price_searches')}
    if 'cache_key' not in existing:
        with op.batch_alter_table('price_searches', schema=None) as batch_op:
            batch_op.add_column(sa.Column('cache_key', sa.String(length=64), nullable=True))
    indexes = {index['name'] for index in sa.inspect(op.get_bind()).get_indexes('price_searches')}
    if 'ix_price_searches_cache_key' not in indexes:
        op.create_index('ix_price_searches_cache_key', 'price_searches', ['cache_key'])

    with op.batch_alter_table('supplier_offers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('price_search_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_supplier_offers_price_search_id'), ['price_search_id'], unique=False)
        batch_op.create_foreign_key('fk_supplier_offers_price_search', 'price_searches', ['price_search_id'], ['id'])

    with op.batch_alter_table('suppliers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('search_url_template', sa.Text(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('suppliers', schema=None) as batch_op:
        batch_op.drop_column('search_url_template')

    with op.batch_alter_table('supplier_offers', schema=None) as batch_op:
        batch_op.drop_constraint('fk_supplier_offers_price_search', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_supplier_offers_price_search_id'))
        batch_op.drop_column('price_search_id')

    with op.batch_alter_table('price_searches', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_price_searches_cache_key'))
        batch_op.drop_column('cache_key')

