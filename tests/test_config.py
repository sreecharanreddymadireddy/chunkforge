"""Tests for the Phase 0 configuration boundary."""

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from chunkforge.config import Settings

REQUIRED_ENVIRONMENT = {
    "CHUNKFORGE_ENVIRONMENT": "test",
    "CHUNKFORGE_LOG_LEVEL": "DEBUG",
    "CHUNKFORGE_POSTGRES_DSN": "postgresql://test:test@localhost:5432/test",
    "CHUNKFORGE_REDIS_URL": "redis://localhost:6379/1",
    "CHUNKFORGE_KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
    "CHUNKFORGE_OLLAMA_BASE_URL": "http://localhost:11434",
    "CHUNKFORGE_OLLAMA_MODEL": "llama3.1",
    "CHUNKFORGE_EMBEDDING_MODEL": "BAAI/bge-small-en-v1.5",
    "CHUNKFORGE_RERANKER_MODEL": "cross-encoder/ms-marco-MiniLM-L-6-v2",
    "CHUNKFORGE_RAW_DATA_DIR": "data/raw",
    "CHUNKFORGE_PROCESSED_DATA_DIR": "data/processed",
}


@pytest.fixture
def settings_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Provide a complete test environment without reading a developer's .env file."""

    for key, value in REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(key, value)
    yield


def test_settings_are_loaded_from_environment(settings_environment: None) -> None:
    """All configuration fields should be typed values sourced from the environment."""

    settings = Settings(_env_file=None)

    assert settings.environment == "test"
    assert settings.postgres_dsn.hosts()[0]["host"] == "localhost"
    assert settings.raw_data_dir.as_posix() == "data/raw"


def test_missing_environment_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Startup should fail fast rather than silently inventing service configuration."""

    for key in REQUIRED_ENVIRONMENT:
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
