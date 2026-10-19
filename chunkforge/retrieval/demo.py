"""Run inspectable real hybrid search and optional controlled dense-dependency failure."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from typing import cast

from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from chunkforge.chunking.model_backend import BGEBackend
from chunkforge.config import get_settings
from chunkforge.embeddings.embedder import Embedder
from chunkforge.resilience.breaker import CircuitBreaker
from chunkforge.resilience.cache import CacheClient, EmbeddingCache
from chunkforge.resilience.retry import RetryPolicy
from chunkforge.retrieval.bm25 import BM25Index
from chunkforge.retrieval.pipeline import RetrievalPipeline
from chunkforge.retrieval.reranker import CrossEncoderScorer, Reranker
from chunkforge.store.pgvector_store import STRATEGIES, PgvectorStore, SearchResult


def main() -> None:
    """Load one corpus snapshot, then print each retrieval stage and cache timings as JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, action="append")
    parser.add_argument("--strategy", choices=STRATEGIES, default="recursive")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--fail-dense", action="store_true", help="Inject an explicit model outage")
    parser.add_argument("--output", type=Path, help="Write measured stage traces as JSON")
    parser.add_argument("--verbose", action="store_true", help="Print all candidate excerpts")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("Repeat must be positive")
    settings = get_settings()
    backend = BGEBackend(
        settings.embedding_model, settings.embedding_batch_size, settings.embedding_device
    )
    revision = backend.transformer.auto_model.config._commit_hash
    client = Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        socket_connect_timeout=settings.redis_timeout_seconds,
        socket_timeout=settings.redis_timeout_seconds,
        retry=Retry(NoBackoff(), 0),
    )
    cache = EmbeddingCache(cast(CacheClient, client), settings.cache_ttl_seconds)
    embedder = Embedder(
        backend,
        settings.embedding_model + "@" + str(revision),
        settings.embedding_dimensions,
        settings.embedding_batch_size,
        settings.embedding_query_instruction,
        cache,
    )
    store = PgvectorStore(
        str(settings.postgres_dsn),
        settings.embedding_dimensions,
        settings.vector_table,
        settings.hnsw_ef_search,
    )
    chunks = store.lexical_snapshot(
        args.strategy, embedder.model_id, embedder.representation(args.strategy)
    )
    if not chunks:
        parser.error("No compatible indexed chunks; run make embed first")
    lexical = BM25Index(chunks)
    snapshot_document_ids = sorted({c.doc_id for c in chunks})
    # Historical example env files contained an extra hyphen; resolve that exact typo only.
    model_name = settings.reranker_model.replace("ms-marco-MiniLM-L-6-v2", "ms-marco-MiniLM-L6-v2")
    scorer = CrossEncoderScorer(model_name, settings.embedding_device)

    timings: list[dict[str, float]] = []

    def dense(query: str, strategy: str, limit: int) -> list[SearchResult]:
        """Protect the full query-embedding plus vector-search dependency boundary."""
        if args.fail_dense:
            raise OSError("Controlled embedding outage")
        started = perf_counter()
        vector = embedder.embed_query(query, strategy)
        embedding_ms = (perf_counter() - started) * 1000
        started = perf_counter()
        results = store.search(
            vector,
            strategy,
            embedder.model_id,
            embedder.representation(strategy),
            limit,
            document_ids=snapshot_document_ids,
        )
        timings.append(
            {
                "query_embedding_ms": round(embedding_ms, 3),
                "vector_search_ms": round((perf_counter() - started) * 1000, 3),
            }
        )
        return results

    pipeline = RetrievalPipeline(
        dense,
        lexical,
        Reranker(scorer),
        args.strategy,
        settings.retrieval_candidates,
        settings.rerank_candidates,
        settings.retrieval_limit,
        settings.rrf_k,
        RetryPolicy(settings.retry_attempts, settings.retry_base_delay, settings.retry_max_delay),
        CircuitBreaker(settings.breaker_failure_threshold, settings.breaker_recovery_seconds),
    )
    metadata = {
        "snapshot_chunks": len(chunks),
        "snapshot_documents": len({c.doc_id for c in chunks}),
        "strategy": args.strategy,
        "reranker_model": scorer.model_id,
    }
    print(json.dumps(metadata), flush=True)
    traces: list[dict[str, object]] = []
    try:
        for query in args.query:
            for _ in range(args.repeat):
                timings.clear()
                started = perf_counter()
                result = pipeline.search(query)
                trace: dict[str, object] = {
                    "query": query,
                    "milliseconds": round((perf_counter() - started) * 1000, 3),
                    "cache_hits": cache.hits,
                    "cache_misses": cache.misses,
                    "cache_errors": cache.errors,
                    "breaker": pipeline.breaker.state,
                    "dense_timings": list(timings),
                    **asdict(result),
                }
                # CLI traces show excerpts; the pipeline itself retains complete evidence text.
                for stage in ("hits", "dense", "lexical", "fused"):
                    for item in cast(list[dict[str, object]], trace[stage]):
                        chunk = cast(dict[str, object], item.get("chunk", item))
                        chunk["text"] = str(chunk["text"])[:400]
                        if chunk.get("context_text") is not None:
                            chunk["context_text"] = str(chunk["context_text"])[:400]
                traces.append(trace)
                summary = {
                    key: value
                    for key, value in trace.items()
                    if key not in ("dense", "lexical", "fused")
                }
                summary["stage_counts"] = {
                    "dense": len(result.dense),
                    "lexical": len(result.lexical),
                    "fused": len(result.fused),
                }
                print(
                    json.dumps(trace if args.verbose else summary, ensure_ascii=False), flush=True
                )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps({**metadata, "traces": traces}, ensure_ascii=False, indent=2) + "\n"
            )
    finally:
        client.close()


if __name__ == "__main__":
    main()
