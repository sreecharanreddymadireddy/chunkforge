"""Opt-in real PostgreSQL tests using isolated, uniquely named disposable tables."""

import json
import os
from collections.abc import Iterator
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from chunkforge.chunking.base import Chunk
from chunkforge.config import get_settings
from chunkforge.embeddings.embedder import EmbeddedChunk
from chunkforge.store.pgvector_store import PgvectorStore

pytestmark = pytest.mark.integration


@pytest.fixture
def store() -> Iterator[PgvectorStore]:
    """Create and remove only tables allocated by this test; leave corpus data intact."""
    if os.environ.get("CHUNKFORGE_RUN_PGVECTOR_TESTS") != "1":
        pytest.skip("Set CHUNKFORGE_RUN_PGVECTOR_TESTS=1 to use the configured local database")
    table_name = "chunkforge_test_" + uuid4().hex[:12]
    dsn = os.environ.get("CHUNKFORGE_TEST_POSTGRES_DSN") or str(get_settings().postgres_dsn)
    repository = PgvectorStore(dsn, 2, table_name)
    repository.create_schema()
    try:
        yield repository
    finally:
        with psycopg.connect(repository.dsn) as conn:
            conn.execute(sql.SQL("DROP TABLE {}").format(repository.table))
            conn.execute(sql.SQL("DROP TABLE {}").format(repository.runs))


def test_real_cosine_search_hnsw_resume_and_rollback(store: PgvectorStore) -> None:
    """Verify numeric ranking, idempotency, HNSW plan and atomic failed replacement."""
    vectors = [(1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    records = [
        EmbeddedChunk(
            Chunk(f"evidence {i}", "doc", i, i * 10, i * 10 + 10, 3, "fixed"),
            vector,
            "test-model@revision",
            "bounded_piece_mean",
        )
        for i, vector in enumerate(vectors)
    ]
    store.replace_document("doc", "fixed", records, "source-config-model-fingerprint")
    assert store.counts() == {"fixed": 3}
    assert store.has_document("doc", "fixed", "source-config-model-fingerprint")
    assert not store.has_document("doc", "fixed", "changed-fingerprint")
    result = store.search((1.0, 0.0), "fixed", "test-model@revision", "bounded_piece_mean")
    assert [r.chunk_index for r in result] == [0, 1, 2]
    assert [r.score for r in result] == pytest.approx([1.0, 2**-0.5, 0.0])
    assert store.search((1.0, 0.0), "fixed", "another-model", "bounded_piece_mean") == []
    store.replace_document("doc", "fixed", records, "source-config-model-fingerprint")
    assert store.counts() == {"fixed": 3}
    invalid = [replace(records[0], chunk=replace(records[0].chunk, token_count=0))]
    with pytest.raises(psycopg.errors.CheckViolation):
        store.replace_document("doc", "fixed", invalid, "invalid-run")
    assert store.has_document("doc", "fixed", "source-config-model-fingerprint")
    assert store.counts() == {"fixed": 3}
    with psycopg.connect(store.dsn) as conn:
        conn.execute("SET LOCAL enable_seqscan=off")
        row = conn.execute(
            sql.SQL("""
            EXPLAIN (FORMAT JSON) SELECT doc_id FROM {} WHERE strategy='fixed'
            ORDER BY embedding <=> '[1,0]'::vector LIMIT 1
        """).format(store.table)
        ).fetchone()
        assert row is not None
        assert "_fixed_hnsw" in json.dumps(row[0])
