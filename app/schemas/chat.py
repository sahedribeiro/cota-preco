from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Specification(StrictModel):
    name: str = Field(max_length=80)
    value: str = Field(max_length=200)


class PurchaseItemDraft(StrictModel):
    description: str = Field(min_length=1, max_length=500)
    normalized_description: str = Field(min_length=1, max_length=500)
    brand: str | None
    model: str | None
    reference: str | None
    quantity: float | None = Field(gt=0, le=999999999)
    unit: str | None
    specifications: list[Specification] = Field(max_length=20)


class PurchaseDraft(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    items: list[PurchaseItemDraft] = Field(max_length=30)
    conditions: list[str] = Field(max_length=15)


class KotaResponse(StrictModel):
    reply: str = Field(min_length=1, max_length=6000)
    purchase_request: PurchaseDraft | None


class ContextMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=16000)


context_adapter = TypeAdapter(Annotated[list[ContextMessage], Field(max_length=6)])
