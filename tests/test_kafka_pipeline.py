"""Acknowledgement ordering, deterministic poison handling and safe source metadata."""

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pymupdf
import pytest

from chunkforge.chunking.model_backend import ContextTokens
from chunkforge.config import Settings
from chunkforge.embeddings.embedder import EmbeddedChunk
from chunkforge.ingestion import kafka_pipeline as kafka
from chunkforge.ingestion.kafka_pipeline import IngestionEvent, make_event, process_message, publish
from chunkforge.store.pgvector_store import PgvectorStore


class Producer:
    """Explicit delivery callback fixture; no running broker required for these tests."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[dict[str, Any]] = []

    def produce(self, topic: str, **kwargs: Any) -> None:
        self.messages.append({"topic": topic, **kwargs})
        kwargs["on_delivery"](RuntimeError("failed") if self.fail else None, None)

    def flush(self, timeout: int) -> int:
        return 0


class Consumer:
    """Track whether the event's database effect preceded offset commit."""

    def __init__(self) -> None:
        self.commits: list[object] = []

    def commit(self, **kwargs: object) -> None:
        self.commits.append(kwargs)


class Message:
    """Expose only message metadata used by the processing boundary."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def value(self) -> bytes:
        return self.payload

    def topic(self) -> str:
        return "input"

    def partition(self) -> int:
        return 0

    def offset(self) -> int:
        return 7


def test_safe_metadata_and_acknowledged_delivery(tmp_path: Path) -> None:
    """Events contain metadata, not PDF bytes, and cannot escape the configured directory."""
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-fixture")
    event = make_event(source, tmp_path, 100)
    assert event.doc_id == "paper" and len(event.sha256) == 64
    assert make_event(source, tmp_path, 100).event_id == event.event_id
    publish(Producer(), "input", event.doc_id, event.model_dump())
    with pytest.raises(RuntimeError):
        publish(Producer(True), "input", event.doc_id, event.model_dump())
    with pytest.raises(ValueError):
        make_event(source, tmp_path, 1)
    with pytest.raises(ValueError):
        make_event(source, tmp_path / "different", 100)


def test_commit_only_after_processing_and_no_commit_on_operational_failure() -> None:
    """Failed storage leaves the offset uncommitted, so a restart can replay safely."""
    event = IngestionEvent(
        doc_id="paper", relative_path="paper.pdf", sha256="a" * 64, event_id="b" * 64
    )
    msg, consumer = Message(event.model_dump_json().encode()), Consumer()

    def success(e: IngestionEvent) -> list[dict[str, object]]:
        assert not consumer.commits
        return [{"status": "stored"}]

    assert process_message(consumer, Producer(), msg, "dlq", success)["status"] == "indexed"
    assert len(consumer.commits) == 1
    consumer.commits.clear()

    def failed(e: IngestionEvent) -> list[dict[str, object]]:
        raise OSError("database offline")

    with pytest.raises(OSError):
        process_message(consumer, Producer(), msg, "dlq", failed)
    assert not consumer.commits


def test_poison_event_commits_only_after_dlq_ack() -> None:
    """A malformed event cannot block the partition forever or be lost without quarantine."""
    consumer, producer = Consumer(), Producer()
    result = process_message(consumer, producer, Message(b"not-json"), "dlq", lambda event: [])
    assert result["status"] == "quarantined" and len(consumer.commits) == 1
    assert json.loads(producer.messages[0]["value"])["source_topic"] == "input"
    consumer.commits.clear()
    with pytest.raises(RuntimeError):
        process_message(consumer, Producer(True), Message(b"not-json"), "dlq", lambda event: [])
    assert not consumer.commits


def test_pdf_parse_all_strategy_storage_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real PDF parsing and all chunkers use a fixture encoder; replay skips identical writes."""

    class Backend:
        model_name = "fixture"
        transformer = SimpleNamespace(
            auto_model=SimpleNamespace(config=SimpleNamespace(_commit_hash="revision"))
        )

        def encode(self, texts: list[str]) -> list[tuple[float, ...]]:
            return [(1.0, 0.0) for _ in texts]

        def contextualize(self, text: str) -> list[ContextTokens]:
            return [
                ContextTokens(
                    0,
                    len(text),
                    tuple((i, i + 1) for i in range(len(text))),
                    tuple((1.0, 0.0) for _ in text),
                )
            ]

    class Store:
        fingerprint = staticmethod(PgvectorStore.fingerprint)

        def __init__(self) -> None:
            self.rows: dict[tuple[str, str], str] = {}

        def create_schema(self) -> None:
            self.created = True

        def has_document(self, doc: str, strategy: str, fingerprint: str) -> bool:
            return self.rows.get((doc, strategy)) == fingerprint

        def replace_document(
            self, doc: str, strategy: str, records: list[EmbeddedChunk], fingerprint: str
        ) -> None:
            assert records and self.created
            self.rows[doc, strategy] = fingerprint

    store = Store()
    monkeypatch.setattr(kafka, "BGEBackend", lambda *args: Backend())
    monkeypatch.setattr(kafka, "PgvectorStore", lambda *args: store)
    processed = tmp_path / "processed"
    settings = SimpleNamespace(
        raw_data_dir=tmp_path,
        processed_data_dir=processed,
        embedding_model="fixture",
        embedding_batch_size=2,
        embedding_device="cpu",
        embedding_dimensions=2,
        embedding_query_instruction="search: ",
        postgres_dsn="unused",
        vector_table="chunkforge_fixture",
        hnsw_ef_search=100,
        token_encoding="cl100k_base",
        chunk_size=32,
        chunk_overlap=4,
        semantic_threshold=0.5,
        sentence_window_size=1,
        late_chunk_size=8,
        kafka_max_pdf_bytes=100000,
    )
    path = tmp_path / "event.pdf"
    with cast(Any, pymupdf).open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "Redis caches vectors. PostgreSQL stores source evidence.")
        pdf.save(path)
    processor = kafka.PDFProcessor(cast(Settings, settings))
    event = make_event(path, tmp_path, 100000)
    outcomes = processor(event)
    assert len(store.rows) == 5 and all(r["status"] == "stored" for r in outcomes)
    assert all(r["status"] == "unchanged" for r in processor(event))
    assert (processed / "event.json").exists()
    invalid = event.model_copy(update={"sha256": "0" * 64})
    with pytest.raises(ValueError):
        processor(invalid)


def test_watcher_baseline_then_only_new_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restarting the watcher uses persistent signatures instead of enqueueing all old PDFs."""
    (tmp_path / "old.pdf").write_bytes(b"%PDF-old")
    settings = SimpleNamespace(
        raw_data_dir=tmp_path,
        kafka_watcher_state=tmp_path / "state.json",
        kafka_watch_interval=0.001,
        kafka_max_pdf_bytes=100,
        kafka_ingestion_topic="input",
    )
    monkeypatch.setattr(time, "sleep", lambda delay: None)
    producer = Producer()
    kafka.watch(cast(Settings, settings), producer, False, True)
    assert not producer.messages
    (tmp_path / "new.pdf").write_bytes(b"%PDF-new")
    kafka.watch(cast(Settings, settings), producer, False, True)
    assert len(producer.messages) == 1
    assert json.loads(producer.messages[0]["value"])["doc_id"] == "new"
    kafka.watch(cast(Settings, settings), producer, False, True)
    assert len(producer.messages) == 1
