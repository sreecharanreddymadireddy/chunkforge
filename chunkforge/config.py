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
    ocr_mode: Literal["off", "auto", "force"] = "off"
    ocr_language: str = Field(default="eng", pattern=r"^[a-zA-Z0-9_]+(?:\+[a-zA-Z0-9_]+)*$")
    ocr_dpi: int = Field(default=200, ge=72, le=400)
    ocr_min_native_characters: int = Field(default=32, ge=1)
    ocr_min_image_coverage: float = Field(default=0.6, gt=0, le=1)
    ocr_max_pages: int = Field(default=50, ge=1)
    ocr_max_pixels: int = Field(default=20_000_000, ge=1)
    ocr_tessdata: Path | None = None
    token_encoding: str = "cl100k_base"
    chunk_size: int = Field(default=512, ge=1)
    chunk_overlap: int = Field(default=50, ge=0)
    semantic_threshold: float = Field(default=0.5, ge=-1, le=1)
    sentence_window_size: int = Field(default=1, ge=0)
    late_chunk_size: int = Field(default=128, ge=1)
    embedding_batch_size: int = Field(default=32, ge=1)
    embedding_device: str = "cpu"
    embedding_dimensions: int = Field(default=384, ge=1, le=2000)
    vector_table: str = "chunkforge_chunks"
    hnsw_ef_search: int = Field(default=100, ge=1, le=1000)
    embedding_query_instruction: str = "Represent this sentence for searching relevant passages: "
    retrieval_candidates: int = Field(default=50, ge=1, le=100)
    rerank_candidates: int = Field(default=20, ge=1, le=100)
    retrieval_limit: int = Field(default=5, ge=1, le=100)
    rrf_k: int = Field(default=60, ge=1)
    cache_ttl_seconds: int = Field(default=86400, ge=1)
    redis_timeout_seconds: float = Field(default=0.25, gt=0)
    retry_attempts: int = Field(default=3, ge=1)
    retry_base_delay: float = Field(default=0.1, ge=0)
    retry_max_delay: float = Field(default=1.0, ge=0)
    breaker_failure_threshold: int = Field(default=3, ge=1)
    breaker_recovery_seconds: float = Field(default=30, gt=0)
    generation_context_size: int = Field(default=2048, ge=1024)
    generation_output_tokens: int = Field(default=192, ge=32)
    generation_template_reserve: int = Field(default=256, ge=128)
    generation_source_bytes: int = Field(default=700, ge=40)
    generation_temperature: float = Field(default=0, ge=0, le=2)
    generation_timeout_seconds: float = Field(default=180, gt=0)
    generation_threads: int = Field(default=2, ge=1)
    cors_origins: list[str] = ["http://localhost:4200"]
    kafka_ingestion_topic: str = "chunkforge.ingestion"
    kafka_dlq_topic: str = "chunkforge.ingestion.dlq"
    kafka_group_id: str = "chunkforge-ingestion"
    kafka_max_pdf_bytes: int = Field(default=52428800,ge=1)
    kafka_watch_interval: float = Field(default=2.0,gt=0)
    kafka_watcher_state: Path = Path("data/processed/kafka_watcher.json")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one immutable-by-convention settings instance per process."""

    return Settings()
