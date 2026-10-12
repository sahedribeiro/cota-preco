from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


def website_url(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Indica um URL http:// ou https:// sem credenciais.")
    if parts.port not in {None, 80, 443}:
        raise ValueError("Porta não suportada.")
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))


class SupplierDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(max_length=200)
    website: str | None = Field(max_length=1000)
    country: str | None = Field(max_length=100)
    category: str | None = Field(max_length=100)
    email: str | None = Field(max_length=200)
    phone: str | None = Field(max_length=100)
    tax_id: str | None = Field(max_length=100)
    notes: str | None = Field(max_length=3000)


class SupplierInput(SupplierDraft):
    name: str = Field(min_length=1, max_length=200)
    search_url_template: str | None = Field(default=None, max_length=1000)
    source_kind: Literal["manual", "user_list", "purchase_order", "proforma", "website"] = "manual"
    source_reference: str | None = Field(default=None, max_length=1000)
    reviewed: bool = False
    active: bool = True
    scraping_enabled: bool = False

    @field_validator("website")
    @classmethod
    def validate_website(cls, value: str | None) -> str | None:
        return website_url(value)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("O nome não pode ficar vazio.")
        return value

    @field_validator("search_url_template")
    @classmethod
    def validate_search_template(cls, value: str | None) -> str | None:
        if not value or not value.strip():
            return None
        if value.count("{query}") != 1 or "{" in value.replace("{query}", "") or "}" in value.replace("{query}", ""):
            raise ValueError("O URL de pesquisa deve conter exactamente {query}.")
        website_url(value.replace("{query}", "produto"))
        return value.strip()
