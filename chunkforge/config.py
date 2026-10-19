"""Environment-driven application configuration.

No service address, model choice, credential, or data path is embedded in Python
code. Pydantic validates the process environment once and returns a typed object,
similar to Spring Boot's ``@ConfigurationProperties``.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated settings loaded from ``CHUNKFORGE_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CHUNKFORGE_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["local", "test", "production"]
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    postgres_dsn: PostgresDsn
    redis_url: RedisDsn
    kafka_bootstrap_servers: str = Field(min_length=1)
    ollama_base_url: AnyHttpUrl
    ollama_model: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    reranker_model: str = Field(min_length=1)
    raw_data_dir: Path
    processed_data_dir: Path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one immutable-by-convention settings instance per process."""

    return Settings()
