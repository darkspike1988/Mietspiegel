"""Application configuration loaded from environment variables.

Single source of truth — no magic strings anywhere else in the codebase.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Override via env vars or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # LLM (Mistral)
    # ------------------------------------------------------------------
    mistral_api_key: str = Field(
        default="",
        description="Mistral API key. Empty = demo mode (AI endpoints return 503).",
    )
    mistral_chat_model: str = "mistral-large-latest"
    mistral_ocr_model: str = "mistral-ocr-latest"
    mistral_timeout_seconds: float = 30.0
    mistral_max_retries: int = 3

    # ------------------------------------------------------------------
    # Server
    # ------------------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    env: str = "development"  # development | production

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------
    allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:4321",  # Astro dev
            "http://localhost:8000",
            "https://mietspiegel.app",
            # Tailscale mesh — frontend served from any Tailscale peer
            "http://zimacube:4321",
            "http://zimacube.taila1334a.ts.net:4321",
        ]
    )

    # ------------------------------------------------------------------
    # Cache (optional Redis)
    # ------------------------------------------------------------------
    redis_url: str = ""  # Empty = in-memory LRU
    cache_ttl_seconds: int = 60 * 60 * 24  # 24h

    # ------------------------------------------------------------------
    # Rate Limit
    # ------------------------------------------------------------------
    rate_limit_per_minute: int = 10

    # ------------------------------------------------------------------
    # Auth (optional API key)
    # ------------------------------------------------------------------
    api_key: str = ""  # If set, requests need X-API-Key header

    # ------------------------------------------------------------------
    # Upload limits
    # ------------------------------------------------------------------
    max_upload_mb: int = 10
    pdf_parser_timeout_sec: int = 30


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor (avoid re-reading env on every request)."""
    return Settings()


settings = get_settings()