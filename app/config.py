from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.models import RunMode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    app_mode: RunMode = RunMode.DEMO
    database_url: str = "postgresql+asyncpg://giftbot:giftbot@localhost:5432/giftbot"
    redis_url: str = "redis://localhost:6379/0"
    scan_interval_seconds: int = Field(default=60, ge=15, le=3600)
    listing_max_age_seconds: int = Field(default=300, ge=30, le=86400)
    min_discount_percent: Decimal = Field(default=Decimal("10"), ge=Decimal("0"), lt=Decimal("100"))
    min_trait_comparables: int = Field(default=3, ge=1, le=100)
    portals_api_key: SecretStr | None = None
    portals_max_pages: int = Field(default=5, ge=1, le=100)
    log_level: str = "INFO"
    log_file: str = "data/nft-monitor.log"

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the asyncpg PostgreSQL driver")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
