from pydantic import BaseModel, ConfigDict, Field
from app.schemas.proforma import ProformaLine

class CatalogLine(ProformaLine):
    confidence: int=Field(ge=0,le=100)
    vat_rate: str | None

class CatalogDraft(BaseModel):
    model_config=ConfigDict(extra='forbid')
    supplier_name: str | None
    supplier_tax_id: str | None
    supplier_email: str | None
    supplier_phone: str | None
    document_date: str | None
    valid_until: str | None
    warnings: str
    lines: list[CatalogLine]
