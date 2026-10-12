from pydantic import BaseModel, ConfigDict, Field


class DocumentLine(BaseModel):
    model_config = ConfigDict(extra='forbid')
    number: str | None
    description: str
    reference: str | None
    quantity: str | None
    unit: str | None
    unit_price: str | None
    line_total: str | None
    currency: str | None
    vat_included: bool | None
    vat_rate: str | None
    evidence: str
    confidence: int = Field(ge=0, le=100)


class ComparisonDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    supplier_name: str | None
    customer_name: str | None
    document_reference: str | None
    document_date: str | None
    valid_until: str | None
    document_total: str | None
    angola_evidence: str | None
    warnings: str
    lines: list[DocumentLine]


class ItemMatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item_index: int
    line_index: int | None
    confidence: int = Field(ge=0, le=100)
    reason: str


class ItemMatches(BaseModel):
    model_config = ConfigDict(extra='forbid')
    matches: list[ItemMatch]
