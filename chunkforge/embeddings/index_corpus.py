"""Resumable document indexing and measured PostgreSQL similarity-search checks."""

import argparse
import hashlib
import json
from statistics import median
from time import perf_counter

from chunkforge.chunking.base import Chunker, TokenCounter
from chunkforge.chunking.fixed import FixedChunker
from chunkforge.chunking.late import LateChunker
from chunkforge.chunking.model_backend import BGEBackend
from chunkforge.chunking.recursive import RecursiveChunker
from chunkforge.chunking.semantic import SemanticChunker
from chunkforge.chunking.sentence_window import SentenceWindowChunker
from chunkforge.config import get_settings
from chunkforge.embeddings.embedder import Embedder
from chunkforge.store.pgvector_store import STRATEGIES, PgvectorStore


def main() -> None:
    """Index complete selected papers, skipping only unchanged, committed strategy runs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-documents", type=int, default=200)
    parser.add_argument("--strategies", nargs="+", choices=STRATEGIES, default=list(STRATEGIES))
    parser.add_argument("--query", default=None, help="Run a real vector query after indexing")
    parser.add_argument("--search-only", action="store_true")
    parser.add_argument("--repetitions", type=int, default=20)
    args = parser.parse_args()
    if args.max_documents < 1 or args.repetitions < 1:
        parser.error("Document count and repetitions must be positive")
    if args.search_only and not args.query:
        parser.error("--search-only requires --query")
    settings = get_settings()
    backend = BGEBackend(
        settings.embedding_model,
        settings.embedding_batch_size,
        settings.embedding_device,
    )
    revision = getattr(backend.transformer.auto_model.config, "_commit_hash", None)
    if not revision:
        raise ValueError("A resolved model revision is required for embedding identity")
    embedder = Embedder(
        backend,
        settings.embedding_model + "@" + str(revision),
        settings.embedding_dimensions,
        settings.embedding_batch_size,
        settings.embedding_query_instruction,
    )
    store = PgvectorStore(
        str(settings.postgres_dsn),
        settings.embedding_dimensions,
        settings.vector_table,
        settings.hnsw_ef_search,
    )
    store.create_schema()
    counter = TokenCounter(settings.token_encoding)
    strategies: dict[str, Chunker] = {
        "fixed": FixedChunker(settings.chunk_size, settings.chunk_overlap, counter),
        "recursive": RecursiveChunker(settings.chunk_size, counter),
        "semantic": SemanticChunker(
            backend,
            settings.chunk_size,
            settings.semantic_threshold,
            counter,
        ),
        "sentence_window": SentenceWindowChunker(
            settings.sentence_window_size,
            settings.chunk_size,
            counter,
        ),
        "late": LateChunker(backend, settings.late_chunk_size, counter),
    }
    config: dict[str, object] = {
        "pipeline_version": 1,
        "token_encoding": settings.token_encoding,
        "chunk_size": settings.chunk_size,
        "overlap": settings.chunk_overlap,
        "semantic_threshold": settings.semantic_threshold,
        "sentence_window": settings.sentence_window_size,
        "late_size": settings.late_chunk_size,
        "dimensions": settings.embedding_dimensions,
    }
    if not args.search_only:
        paths = sorted(settings.processed_data_dir.glob("*.txt"))[: args.max_documents]
        if len(paths) != args.max_documents:
            parser.error(f"Need {args.max_documents} processed documents; found {len(paths)}")
        for path in paths:
            text = path.read_text(encoding="utf-8")
            source_sha = hashlib.sha256(text.encode()).hexdigest()
            for strategy in dict.fromkeys(args.strategies):
                fingerprint = store.fingerprint(
                    source_sha, {**config, "strategy": strategy}, embedder.model_id
                )
                if store.has_document(path.stem, strategy, fingerprint):
                    print(f"skip {path.stem}/{strategy}: unchanged complete run", flush=True)
                    continue
                started = perf_counter()
                chunks = strategies[strategy].chunk(text, path.stem)
                records = embedder.embed_chunks(chunks)
                store.replace_document(path.stem, strategy, records, fingerprint)
                print(
                    json.dumps(
                        {
                            "document": path.stem,
                            "strategy": strategy,
                            "chunks": len(records),
                            "seconds": round(perf_counter() - started, 3),
                        }
                    ),
                    flush=True,
                )
    print("Stored chunks: " + json.dumps(store.counts()), flush=True)
    if args.query:
        for strategy in dict.fromkeys(args.strategies):
            vector = embedder.embed_query(args.query, strategy)
            representation = embedder.representation(strategy)
            store.search(vector, strategy, embedder.model_id, representation)
            latencies = []
            for _ in range(args.repetitions):
                started = perf_counter()
                results = store.search(vector, strategy, embedder.model_id, representation)
                latencies.append((perf_counter() - started) * 1000)
            print(
                json.dumps(
                    {
                        "strategy": strategy,
                        "p50_search_ms": round(median(latencies), 3),
                        "repetitions": args.repetitions,
                        "measurement": "warm database search including connection; "
                        "excludes query encoding",
                        "results": [
                            {
                                "doc_id": r.doc_id,
                                "chunk_index": r.chunk_index,
                                "score": round(r.score, 5),
                                "text": r.text[:400],
                            }
                            for r in results
                        ],
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
