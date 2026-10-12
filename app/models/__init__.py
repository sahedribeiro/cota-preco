from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PurchaseRequest(Base):
    __tablename__ = "purchase_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PurchaseRequestItem(Base):
    __tablename__ = "purchase_request_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_item_quantity_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_request_id: Mapped[int] = mapped_column(ForeignKey("purchase_requests.id"), index=True)
    description: Mapped[str] = mapped_column(Text)
    normalized_description: Mapped[str | None] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(String(150))
    model: Mapped[str | None] = mapped_column(String(150))
    reference: Mapped[str | None] = mapped_column(String(150))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    unit: Mapped[str | None] = mapped_column(String(40))
    specifications_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    website: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(100))
    category: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(100))
    tax_id: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    source_kind: Mapped[str] = mapped_column(String(30), default="manual", server_default="manual")
    source_reference: Mapped[str | None] = mapped_column(Text)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    search_url_template: Mapped[str | None] = mapped_column(Text)
    scraping_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SupplierOffer(Base):
    __tablename__ = "supplier_offers"
    __table_args__ = (
        CheckConstraint("match_confidence BETWEEN 0 AND 100", name="ck_offer_confidence"),
        CheckConstraint("unit_price >= 0", name="ck_offer_price"),
        CheckConstraint("vat_rate BETWEEN 0 AND 100", name="ck_offer_vat"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_line_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    purchase_request_item_id: Mapped[int] = mapped_column(ForeignKey("purchase_request_items.id"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    price_search_id: Mapped[int | None] = mapped_column(ForeignKey("price_searches.id"), index=True)
    product_name: Mapped[str] = mapped_column(String(300))
    product_url: Mapped[str | None] = mapped_column(Text)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3), default="AOA")
    vat_included: Mapped[bool | None] = mapped_column(Boolean)
    vat_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    availability: Mapped[str | None] = mapped_column(Text)
    delivery_information: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    match_confidence: Mapped[int] = mapped_column(Integer, default=0)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    review_history_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, server_default="[]")


class PriceSearch(Base):
    __tablename__ = "price_searches"
    __table_args__ = (Index("ix_search_item_expiry", "purchase_request_item_id", "expires_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_request_item_id: Mapped[int] = mapped_column(ForeignKey("purchase_request_items.id"))
    cache_key: Mapped[str | None] = mapped_column(String(64), index=True)
    search_query: Mapped[str] = mapped_column(Text)
    searched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    raw_result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ProformaImport(Base):
    __tablename__ = 'proforma_imports'

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_request_id: Mapped[int] = mapped_column(ForeignKey('purchase_requests.id'), index=True)
    stored_filename: Mapped[str] = mapped_column(String(80))
    draft_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="Nova conversa")
    purchase_request_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_requests.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (CheckConstraint("role IN ('user', 'assistant', 'system')", name="ck_message_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class UsageRecord(Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        CheckConstraint("input_tokens >= 0 AND output_tokens >= 0", name="ck_usage_tokens"),
        CheckConstraint("estimated_cost >= 0", name="ck_usage_cost"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SalesQuote(Base):
    __tablename__ = 'sales_quotes'
    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_request_id: Mapped[int] = mapped_column(ForeignKey('purchase_requests.id'), unique=True)
    company_json: Mapped[dict] = mapped_column(JSON, default=dict)
    customer: Mapped[str] = mapped_column(Text, default='')
    terms: Mapped[str] = mapped_column(Text, default='')
    pricing_method: Mapped[str] = mapped_column(String(20))
    percentage: Mapped[Decimal] = mapped_column(Numeric(8, 3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SalesQuoteLine(Base):
    __tablename__ = 'sales_quote_lines'
    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey('sales_quotes.id'), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey('purchase_request_items.id'))
    description: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(40))
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    currency: Mapped[str] = mapped_column(String(3))
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    sale_price: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    source_json: Mapped[dict] = mapped_column(JSON)


class Product(Base):
    __tablename__='products'
    id: Mapped[int]=mapped_column(primary_key=True)
    name: Mapped[str]=mapped_column(String(500))
    brand: Mapped[str | None]=mapped_column(String(150))
    model: Mapped[str | None]=mapped_column(String(150))
    reference: Mapped[str | None]=mapped_column(String(150),index=True)
    category: Mapped[str | None]=mapped_column(String(150))
    unit: Mapped[str | None]=mapped_column(String(40))
    specifications_json: Mapped[dict]=mapped_column(JSON,default=dict)
    aliases_json: Mapped[list]=mapped_column(JSON,default=list)


class SupplierProduct(Base):
    __tablename__='supplier_products'
    id: Mapped[int]=mapped_column(primary_key=True)
    supplier_id: Mapped[int]=mapped_column(ForeignKey('suppliers.id'),index=True)
    product_id: Mapped[int | None]=mapped_column(ForeignKey('products.id'),index=True)
    identity_key: Mapped[str]=mapped_column(String(64),unique=True)
    description: Mapped[str]=mapped_column(Text)
    reference: Mapped[str | None]=mapped_column(String(150))
    unit: Mapped[str | None]=mapped_column(String(40))


class PriceRecord(Base):
    __tablename__='price_records'
    __table_args__=(CheckConstraint('unit_price > 0',name='ck_catalog_price_positive'),)
    id: Mapped[int]=mapped_column(primary_key=True)
    supplier_product_id: Mapped[int]=mapped_column(ForeignKey('supplier_products.id'),index=True)
    unit_price: Mapped[Decimal | None]=mapped_column(Numeric(18,2))
    currency: Mapped[str | None]=mapped_column(String(3))
    vat_included: Mapped[bool | None]=mapped_column(Boolean)
    vat_rate: Mapped[Decimal | None]=mapped_column(Numeric(5,2))
    price_date: Mapped[datetime | None]=mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None]=mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc_now)
    source_kind: Mapped[str]=mapped_column(String(30))
    source_reference: Mapped[str | None]=mapped_column(Text)
    evidence: Mapped[str]=mapped_column(Text,default='')
    origin_key: Mapped[str]=mapped_column(String(160),unique=True)
    status: Mapped[str]=mapped_column(String(20),default='accepted')
    raw_json: Mapped[dict]=mapped_column(JSON,default=dict)


class CatalogImport(Base):
    __tablename__='catalog_imports'
    id: Mapped[int]=mapped_column(primary_key=True)
    supplier_id: Mapped[int | None]=mapped_column(ForeignKey('suppliers.id'))
    filename: Mapped[str]=mapped_column(String(100))
    source_kind: Mapped[str]=mapped_column(String(30))
    draft_json: Mapped[dict]=mapped_column(JSON,default=dict)
    status: Mapped[str]=mapped_column(String(30),default='pending')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc_now)


class AppUser(Base):
    __tablename__='app_users'
    id: Mapped[int]=mapped_column(primary_key=True)
    username: Mapped[str]=mapped_column(String(100),unique=True)
    password_hash: Mapped[str]=mapped_column(Text)
    active: Mapped[bool]=mapped_column(Boolean,default=True)
    failed_attempts: Mapped[int]=mapped_column(Integer,default=0)
    locked_until: Mapped[datetime | None]=mapped_column(DateTime(timezone=True))


class LoginSession(Base):
    __tablename__='login_sessions'
    token_hash: Mapped[str]=mapped_column(String(64),primary_key=True)
    user_id: Mapped[int]=mapped_column(ForeignKey('app_users.id'),index=True)
    expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
