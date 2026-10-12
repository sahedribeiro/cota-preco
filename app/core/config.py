from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'cota-preco-ai.db').as_posix()}"
    upload_path: Path = BASE_DIR / "data" / "uploads"
    upload_max_size_mb: int = Field(default=10, ge=1, le=25)
    document_max_chars: int = Field(default=8000, ge=100, le=10000)
    auth_enabled: bool = True
    auth_cookie_secure: bool = False
    catalog_stale_days: int = Field(default=60, ge=1)
    price_search_cache_hours: int = Field(default=24, ge=1)
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4.1-mini"
    openai_max_output_tokens: int = Field(default=2500, ge=256, le=8000)
    openai_timeout_seconds: float = Field(default=40, ge=1, le=120)


settings = Settings()
