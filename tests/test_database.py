from datetime import timedelta
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import BASE_DIR, settings
from app.core.database import build_engine
from app.models import (
    Conversation, Message, PriceSearch, PurchaseRequest, PurchaseRequestItem,
    Supplier, SupplierOffer, UsageRecord, UserPreference, utc_now,
)


@pytest.fixture
def migrated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    config = Config(str(BASE_DIR / "alembic.ini"))
    command.upgrade(config, "head")
    engine = build_engine(settings.database_url)
    yield engine, config
    engine.dispose()


def test_migration_roundtrip(migrated_db):
    engine, config = migrated_db
    tables = inspect(engine).get_table_names()
    assert set(tables) == {
        "alembic_version", "purchase_requests", "purchase_request_items", "suppliers",
        "supplier_offers", "price_searches", "conversations", "messages", "user_preferences", "usage_records", "proforma_imports", "sales_quotes", "sales_quote_lines", "products", "supplier_products", "price_records", "catalog_imports", "app_users", "login_sessions",
    }
    command.check(config)
    command.downgrade(config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    command.upgrade(config, "head")
    assert inspect(engine).get_table_names() == tables


def test_persist_all_models(migrated_db):
    engine, _ = migrated_db
    with Session(engine) as session:
        request = PurchaseRequest(title="Computadores")
        supplier = Supplier(name="Fornecedor de teste")
        session.add_all([request, supplier])
        session.flush()
        item = PurchaseRequestItem(purchase_request_id=request.id, description="PC", quantity=Decimal("10"), specifications_json={"ram_gb": 16})
        conversation = Conversation(purchase_request_id=request.id)
        session.add_all([item, conversation])
        session.flush()
        session.add_all([
            SupplierOffer(purchase_request_item_id=item.id, supplier_id=supplier.id, product_name="PC de teste", unit_price=Decimal("1234.56"), match_confidence=96),
            PriceSearch(purchase_request_item_id=item.id, search_query="PC", expires_at=utc_now() + timedelta(hours=24)),
            Message(conversation_id=conversation.id, role="user", content="Olá"),
            UserPreference(key="currency", value_json={"value": "AOA"}),
            UsageRecord(conversation_id=conversation.id, provider="test", model="test"),
        ])
        session.commit()
    with Session(engine) as session:
        offer = session.scalars(select(SupplierOffer)).one()
        assert offer.unit_price == Decimal("1234.56")
        assert offer.vat_included is None
        assert offer.availability is None
        assert session.scalars(select(PurchaseRequestItem)).one().specifications_json == {"ram_gb": 16}
        assert session.scalars(select(Message)).one().content == "Olá"
        offer.match_confidence = 101
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_foreign_key_enforced(migrated_db):
    engine, _ = migrated_db
    with Session(engine) as session:
        session.add(Message(conversation_id=999, role="user", content="Invalid"))
        with pytest.raises(IntegrityError):
            session.commit()
