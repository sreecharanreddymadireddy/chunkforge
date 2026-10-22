"""Local PDF events with acknowledged production, replay-safe indexing and manual commits."""

import argparse
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field
from pymupdf import FileDataError

from chunkforge.chunking.model_backend import BGEBackend
from chunkforge.config import Settings, get_settings
from chunkforge.embeddings.embedder import Embedder
from chunkforge.embeddings.indexer import DocumentIndexer
from chunkforge.ingestion.ocr import OcrOptions
from chunkforge.ingestion.parser import parse_pdf, write_parsed_document
from chunkforge.store.pgvector_store import STRATEGIES, PgvectorStore

Strategy = Literal["fixed", "recursive", "semantic", "sentence_window", "late"]


class IngestionEvent(BaseModel):
    """Only safe relative file names travel through Kafka; PDF bytes remain in shared storage."""

    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    doc_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,120}$")
    relative_path: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,120}\.pdf$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    event_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    strategies: list[Strategy] = Field(
        default_factory=lambda: cast(list[Strategy], list(STRATEGIES)), min_length=1
    )


def file_hash(path: Path) -> str:
    """Stream file hashing rather than holding a whole PDF in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def make_event(path: Path, raw_dir: Path, max_bytes: int) -> IngestionEvent:
    """Reject paths outside the shared raw directory and oversized/partial files."""
    resolved = path.resolve()
    root = raw_dir.resolve()
    if resolved.parent != root or path.suffix != ".pdf" or path.stat().st_size > max_bytes:
        raise ValueError("PDF must be a bounded file directly inside the raw directory")
    digest = file_hash(path)
    identity = hashlib.sha256(json.dumps([path.stem, digest]).encode()).hexdigest()
    return IngestionEvent(
        doc_id=path.stem, relative_path=path.name, sha256=digest, event_id=identity
    )


def kafka_producer(settings: Settings) -> Any:
    """SDK types stay at the boundary; business events remain Pydantic-validated."""
    from confluent_kafka import Producer

    return Producer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "enable.idempotence": True,
            "acks": "all",
            "delivery.timeout.ms": 10000,
        }
    )


def ensure_topics(settings: Settings) -> None:
    """Single-broker local topics; no deletion or reset of existing offsets."""
    from confluent_kafka import KafkaError, KafkaException
    from confluent_kafka import admin as kafka_admin
    from confluent_kafka.admin import AdminClient

    new_topic = cast(Any, kafka_admin).NewTopic

    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap_servers})
    futures = admin.create_topics(
        [
            new_topic(name, num_partitions=3, replication_factor=1)
            for name in (settings.kafka_ingestion_topic, settings.kafka_dlq_topic)
        ]
    )
    for future in futures.values():
        try:
            future.result(timeout=15)
        except KafkaException as exc:
            if exc.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
                raise


def publish(producer: Any, topic: str, key: str, payload: dict[str, object]) -> None:
    """Do not mark producer state complete until the broker acknowledges delivery."""
    delivered: list[object] = []

    def acknowledged(error: object, message: object) -> None:
        delivered.append(error)

    producer.produce(
        topic, key=key.encode(), value=json.dumps(payload).encode(), on_delivery=acknowledged
    )
    pending = producer.flush(12)
    if pending or not delivered or delivered[0] is not None:
        raise RuntimeError("Kafka delivery was not acknowledged")


def process_message(
    consumer: Any,
    producer: Any,
    message: Any,
    topic: str,
    process: Callable[[IngestionEvent], list[dict[str, object]]],
) -> dict[str, object]:
    """Commit only after storage succeeds or a poison event is durably quarantined."""
    raw = message.value()
    payload = raw if isinstance(raw, bytes) else str(raw or "").encode()
    try:
        event = IngestionEvent.model_validate_json(payload)
        outcomes = process(event)
    except (ValueError, FileNotFoundError, FileDataError) as exc:
        publish(
            producer,
            topic,
            str(message.partition()),
            {
                "source_topic": message.topic(),
                "partition": message.partition(),
                "offset": message.offset(),
                "failure_type": type(exc).__name__,
                "event": payload.decode(errors="replace")[:4000],
            },
        )
        consumer.commit(message=message, asynchronous=False)
        return {
            "status": "quarantined",
            "offset": message.offset(),
            "failure_type": type(exc).__name__,
        }
    consumer.commit(message=message, asynchronous=False)
    return {
        "status": "indexed",
        "event_id": event.event_id,
        "doc_id": event.doc_id,
        "offset": message.offset(),
        "outcomes": outcomes,
    }


class PDFProcessor:
    """Same model/config fingerprint contract as batch indexing; no per-message model loading."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        backend = BGEBackend(
            settings.embedding_model, settings.embedding_batch_size, settings.embedding_device
        )
        revision = backend.transformer.auto_model.config._commit_hash
        if not revision:
            raise ValueError("Resolved embedding revision is required")
        embedder = Embedder(
            backend,
            settings.embedding_model + "@" + str(revision),
            settings.embedding_dimensions,
            settings.embedding_batch_size,
            settings.embedding_query_instruction,
        )
        self.store = PgvectorStore(
            str(settings.postgres_dsn),
            settings.embedding_dimensions,
            settings.vector_table,
            settings.hnsw_ef_search,
        )
        self.store.create_schema()
        self.indexer = DocumentIndexer(settings, backend, embedder, self.store)

    def __call__(self, event: IngestionEvent) -> list[dict[str, object]]:
        """Verify source identity before and after parsing; persist text/provenance and vectors."""
        path = self.settings.raw_data_dir / event.relative_path
        if path.resolve().parent != self.settings.raw_data_dir.resolve():
            raise ValueError("Unsafe source path")
        if path.stem != event.doc_id or file_hash(path) != event.sha256:
            raise ValueError("Source changed or event identity does not match")
        if (
            event.event_id
            != hashlib.sha256(json.dumps([event.doc_id, event.sha256]).encode()).hexdigest()
        ):
            raise ValueError("Invalid event fingerprint")
        if path.stat().st_size > self.settings.kafka_max_pdf_bytes:
            raise ValueError("PDF exceeds event processing budget")
        parsed = parse_pdf(path, ocr=OcrOptions.from_settings(self.settings))
        if not parsed.text.strip() or file_hash(path) != event.sha256:
            raise ValueError("Empty or changed PDF")
        write_parsed_document(parsed, self.settings.processed_data_dir, event.doc_id)
        text = (self.settings.processed_data_dir / (event.doc_id + ".txt")).read_text()
        return [asdict(r) for r in self.indexer.index(text, event.doc_id, list(event.strategies))]


def watch(settings: Settings, producer: Any, include_existing: bool, once: bool) -> None:
    """Persist acknowledged file signatures; a new watcher baselines existing PDFs by default."""
    import fcntl

    state_path = settings.kafka_watcher_state
    state_path.parent.mkdir(parents=True, exist_ok=True)
    root = settings.raw_data_dir

    def signatures() -> dict[str, list[int]]:
        return {
            p.name: [p.stat().st_size, p.stat().st_mtime_ns] for p in sorted(root.glob("*.pdf"))
        }

    def save(state: dict[str, list[int]]) -> None:
        temp = state_path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, sort_keys=True) + "\n")
        temp.replace(state_path)

    with state_path.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        seen = (
            json.loads(state_path.read_text())
            if state_path.exists()
            else ({} if include_existing else signatures())
        )
        save(seen)
        previous = signatures()
        if once:
            time.sleep(settings.kafka_watch_interval)
        while True:
            current = signatures()
            for name, signature in current.items():
                if seen.get(name) == signature or previous.get(name) != signature:
                    continue
                event = make_event(root / name, root, settings.kafka_max_pdf_bytes)
                publish(
                    producer,
                    settings.kafka_ingestion_topic,
                    event.doc_id,
                    event.model_dump(mode="json"),
                )
                seen[name] = signature
                save(seen)
                print(json.dumps({"status": "published", **event.model_dump()}), flush=True)
            if once:
                return
            previous = current
            time.sleep(settings.kafka_watch_interval)


def main() -> None:
    """Run producer/consumer roles separately; configure shared file access explicitly."""
    from confluent_kafka import Consumer

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=["watch", "consume"])
    parser.add_argument("--include-existing", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--max-events", type=int)
    parser.add_argument("--idle-timeout", type=float, default=120)
    args = parser.parse_args()
    if args.max_events is not None and args.max_events < 1:
        parser.error("max-events must be positive")
    s = get_settings()
    ensure_topics(s)
    producer = kafka_producer(s)
    if args.role == "watch":
        watch(s, producer, args.include_existing, args.once)
        return
    processor = PDFProcessor(s)
    consumer = Consumer(
        {
            "bootstrap.servers": s.kafka_bootstrap_servers,
            "group.id": s.kafka_group_id,
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "auto.offset.reset": "earliest",
            "max.poll.interval.ms": 900000,
        }
    )
    consumer.subscribe([s.kafka_ingestion_topic])
    count, last = 0, time.monotonic()
    try:
        while args.max_events is None or count < args.max_events:
            message = consumer.poll(1.0)
            if message is None:
                if args.max_events is not None and time.monotonic() - last > args.idle_timeout:
                    raise TimeoutError("No ingestion event before idle deadline")
                continue
            if message.error():
                raise RuntimeError("Kafka consumer error")
            result = process_message(consumer, producer, message, s.kafka_dlq_topic, processor)
            print(json.dumps(result), flush=True)
            count += 1
            last = time.monotonic()
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
