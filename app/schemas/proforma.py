from pydantic import BaseModel, ConfigDict, Field


class ProformaLine(BaseModel):
    model_config = ConfigDict(extra='forbid')
    description: str = Field(max_length=300)
    reference: str | None = Field(max_length=150)
    quantity: str | None = Field(max_length=40)
    unit: str | None = Field(max_length=40)
    unit_price: str | None = Field(max_length=40)
    currency: str | None = Field(max_length=3)
    vat_included: bool | None
    evidence: str = Field(max_length=600)


class ProformaDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    supplier_name: str | None = Field(max_length=200)
    document_reference: str | None = Field(max_length=150)
    document_date: str | None = Field(max_length=40)
    valid_until: str | None = Field(max_length=40)
    warnings: str = Field(max_length=1500)
    lines: list[ProformaLine]
