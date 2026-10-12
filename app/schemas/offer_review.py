from typing import Literal
from datetime import date
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class OfferReviewInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    reviewer: str = Field(min_length=1, max_length=100)
    notes: str = Field(min_length=1, max_length=2000)
    match: Literal['unknown', 'yes', 'no'] = 'unknown'
    unit: Literal['unknown', 'yes', 'no'] = 'unknown'
    vat: Literal['unknown', 'included', 'excluded'] = 'unknown'
    condition: Literal['unknown', 'new', 'used'] = 'unknown'
    stock: Literal['unknown', 'in', 'out'] = 'unknown'
    action: Literal['save', 'revoke'] = 'save'


class DocumentOfferReviewInput(OfferReviewInput):
    valid_until: date | None = None
    covered_quantity: Decimal | None = Field(default=None, gt=0, le=999999999, decimal_places=3)
    conditions: Literal['unknown', 'yes'] = 'unknown'

    @field_validator("valid_until", "covered_quantity", mode="before")
    @classmethod
    def empty_optional(cls, value: object) -> object:
        return None if value == "" else value


class BatchDocumentReviewInput(DocumentOfferReviewInput):
    offer_ids: list[int] = Field(min_length=1, max_length=100)
    quantity_confirmed: Literal['yes']
    notes: str = Field(default='', max_length=2000)
