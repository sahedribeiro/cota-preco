from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class RequestItemInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal | None = Field(default=None, gt=0, le=999999999, decimal_places=3)
    unit: str = Field(default='', max_length=40)
    brand: str = Field(default='', max_length=150)
    model: str = Field(default='', max_length=150)
    reference: str = Field(default='', max_length=150)
    specifications: str = Field(default='', max_length=6000)

    @field_validator('quantity', mode='before')
    @classmethod
    def parse_quantity(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().replace(',', '.') or None
        return value

    @field_validator('specifications')
    @classmethod
    def validate_specifications(cls, value: str) -> str:
        names = set()
        for line in value.splitlines():
            if not line.strip():
                continue
            name, separator, content = line.partition(':')
            if not separator or not name.strip() or not content.strip() or name.strip() in names:
                raise ValueError('Usa Nome: valor, sem nomes repetidos.')
            names.add(name.strip())
        return value

    def item_values(self) -> dict:
        values = self.model_dump(exclude={'specifications'})
        for field in ('unit', 'brand', 'model', 'reference'):
            values[field] = values[field] or None
        values['normalized_description'] = self.description
        values['specifications_json'] = {line.partition(':')[0].strip(): line.partition(':')[2].strip()
                                         for line in self.specifications.splitlines() if line.strip()}
        return values
