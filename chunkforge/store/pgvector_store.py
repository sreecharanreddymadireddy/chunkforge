"""Transactional, resumable chunk persistence and cosine search using psycopg."""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, cast

import psycopg
from psycopg import sql

from chunkforge.embeddings.embedder import EmbeddedChunk, normalize_vector

STRATEGIES = ("fixed", "recursive", "semantic", "sentence_window", "late")


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Ranked evidence and its original source range; similarity is not confidence."""

    doc_id: str
    chunk_index: int
    text: str
    char_start: int
    char_end: int
    score: float
    context_text: str | None


class PgvectorStore:
    """Small repository abstraction; isolate document replacement in one transaction.

    Each strategy has its own partial HNSW index, avoiding cross-strategy
    post-filtering. A run manifest is committed with its chunks so interrupted
    indexing never marks incomplete work as complete. Existing unrelated tables
    are not used. Connections are short-lived in this batch-oriented phase.
    """

    def __init__(
        self,
        dsn: str,
        dimensions: int = 384,
        table_name: str = "chunkforge_chunks",
        ef_search: int = 100,
    ) -> None:
        if not re.fullmatch(r"chunkforge_[a-z0-9_]{1,36}", table_name):
            raise ValueError("Table must use a short chunkforge_ namespace")
        if not 1 <= dimensions <= 2000 or not 1 <= ef_search <= 1000:
            raise ValueError("Invalid HNSW vector dimensions or ef_search")
        self.dsn = dsn
        self.dimensions = dimensions
        self.table_name = table_name
        self.ef_search = ef_search
        self.table = sql.Identifier(table_name)
        self.runs = sql.Identifier(table_name + "_runs")

    def _connect(self) -> psycopg.Connection[tuple[Any, ...]]:
        """Open a local transaction boundary without logging credentials."""
        return psycopg.connect(self.dsn, connect_timeout=10)

    def _vector(self, vector: tuple[float, ...]) -> str:
        """Bind a validated vector literal as a value, never interpolate it as SQL."""
        return "[" + ",".join(str(x) for x in normalize_vector(vector, self.dimensions)) + "]"

    @staticmethod
    def _strategy(strategy: str) -> None:
        """Whitelist strategy values, including those used in partial-index plans."""
        if strategy not in STRATEGIES:
            raise ValueError("Unknown chunking strategy")

    def create_schema(self) -> None:
        """Create vector storage, resumability manifest and strategy-specific HNSW indexes."""
        with self._connect() as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(
                sql.SQL("""
                CREATE TABLE IF NOT EXISTS {} (
                    doc_id text NOT NULL, strategy text NOT NULL,
                    chunk_index integer NOT NULL CHECK (chunk_index >= 0),
                    text text NOT NULL, char_start integer NOT NULL CHECK (char_start >= 0),
                    char_end integer NOT NULL CHECK (char_end > char_start),
                    token_count integer NOT NULL CHECK (token_count > 0),
                    model_id text NOT NULL, representation text NOT NULL,
                    context_start integer, context_end integer, context_text text,
                    embedding vector({}) NOT NULL,
                    PRIMARY KEY (doc_id, strategy, chunk_index)
                )
            """).format(self.table, sql.Literal(self.dimensions))
            )
            conn.execute(
                sql.SQL("""
                CREATE TABLE IF NOT EXISTS {} (
                    doc_id text NOT NULL, strategy text NOT NULL,
                    fingerprint text NOT NULL, chunk_count integer NOT NULL,
                    PRIMARY KEY (doc_id, strategy)
                )
            """).format(self.runs)
            )
            for strategy in STRATEGIES:
                conn.execute(
                    sql.SQL("""
                    CREATE INDEX IF NOT EXISTS {} ON {} USING hnsw
                    (embedding vector_cosine_ops) WHERE strategy = {}
                """).format(
                        sql.Identifier(self.table_name + "_" + strategy + "_hnsw"),
                        self.table,
                        sql.Literal(strategy),
                    )
                )

    @staticmethod
    def fingerprint(source_sha: str, config: dict[str, object], model_id: str) -> str:
        """Invalidate resumable work when source, strategy configuration or model changes."""
        payload = json.dumps([source_sha, config, model_id], sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def has_document(self, doc_id: str, strategy: str, fingerprint: str) -> bool:
        """Only skip work whose manifest and actual row count still agree."""
        self._strategy(strategy)
        with self._connect() as conn:
            row = conn.execute(
                sql.SQL("""
                SELECT r.chunk_count, (SELECT count(*) FROM {} c
                    WHERE c.doc_id = r.doc_id AND c.strategy = r.strategy)
                FROM {} r WHERE r.doc_id = %s AND r.strategy = %s AND r.fingerprint = %s
            """).format(self.table, self.runs),
                (doc_id, strategy, fingerprint),
            ).fetchone()
            return row is not None and row[0] == row[1]

    def replace_document(
        self,
        doc_id: str,
        strategy: str,
        records: list[EmbeddedChunk],
        fingerprint: str,
    ) -> None:
        """Atomically replace one document/strategy and commit its completion marker."""
        self._strategy(strategy)
        if not records:
            raise ValueError("Cannot mark an empty chunk collection complete")
        rows = []
        for i, record in enumerate(records):
            chunk = record.chunk
            if chunk.doc_id != doc_id or chunk.strategy != strategy or chunk.chunk_index != i:
                raise ValueError("Chunk identity or ordering does not match document replacement")
            rows.append(
                (
                    doc_id,
                    strategy,
                    i,
                    chunk.text,
                    chunk.char_start,
                    chunk.char_end,
                    chunk.token_count,
                    record.model_id,
                    record.representation,
                    chunk.context_start,
                    chunk.context_end,
                    chunk.context_text,
                    self._vector(record.vector),
                )
            )
        with self._connect() as conn:
            # Serialize writers replacing the same source; the lock releases at commit/rollback.
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (self.table_name + ":" + doc_id + ":" + strategy,),
            )
            conn.execute(
                sql.SQL("DELETE FROM {} WHERE doc_id=%s AND strategy=%s").format(self.table),
                (doc_id, strategy),
            )
            with conn.cursor() as cursor:
                cursor.executemany(
                    sql.SQL("""
                    INSERT INTO {} (doc_id, strategy, chunk_index, text, char_start, char_end,
                        token_count, model_id, representation, context_start, context_end,
                        context_text, embedding)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)
                """).format(self.table),
                    rows,
                )
            conn.execute(
                sql.SQL("""
                INSERT INTO {} VALUES (%s,%s,%s,%s)
                ON CONFLICT (doc_id,strategy) DO UPDATE
                SET fingerprint=excluded.fingerprint, chunk_count=excluded.chunk_count
            """).format(self.runs),
                (doc_id, strategy, fingerprint, len(rows)),
            )

    def search(
        self,
        vector: tuple[float, ...],
        strategy: str,
        model_id: str,
        representation: str,
        limit: int = 5,
        document_ids: list[str] | None = None,
    ) -> list[SearchResult]:
        """Rank by cosine distance with an indexable ORDER BY and model/pooling filters."""
        self._strategy(strategy)
        if not 1 <= limit <= 100:
            raise ValueError("Search limit must be between 1 and 100")
        value = self._vector(vector)
        with self._connect() as conn:
            conn.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(self.ef_search),))
            conn.execute("SET LOCAL hnsw.iterative_scan = 'strict_order'")
            rows = conn.execute(
                sql.SQL("""
                SELECT doc_id, chunk_index, text, char_start, char_end,
                       1 - (embedding <=> %s::vector) AS score, context_text
                FROM {} WHERE strategy = {} AND model_id=%s AND representation=%s
                AND (%s::text[] IS NULL OR doc_id = ANY(%s::text[]))
                ORDER BY embedding <=> %s::vector LIMIT %s
            """).format(self.table, sql.Literal(strategy)),
                (value, model_id, representation, document_ids, document_ids, value, limit),
            ).fetchall()
        return [
            SearchResult(
                cast(str, row[0]),
                cast(int, row[1]),
                cast(str, row[2]),
                cast(int, row[3]),
                cast(int, row[4]),
                float(cast(float, row[5])),
                cast(str | None, row[6]),
            )
            for row in rows
        ]

    def counts(self) -> dict[str, int]:
        """Return current stored chunk counts per strategy."""
        with self._connect() as conn:
            rows = conn.execute(
                sql.SQL(
                    "SELECT strategy,count(*) FROM {} GROUP BY strategy ORDER BY strategy",
                ).format(self.table)
            ).fetchall()
        return {cast(str, row[0]): int(cast(int, row[1])) for row in rows}

    def lexical_snapshot(
        self,
        strategy: str,
        model_id: str,
        representation: str,
    ) -> list[SearchResult]:
        """Read a consistent ordered BM25 snapshot from the same embedding-space partition."""
        self._strategy(strategy)
        with self._connect() as conn:
            rows = conn.execute(
                sql.SQL("""
                SELECT doc_id,chunk_index,text,char_start,char_end,context_text
                FROM {} WHERE strategy=%s AND model_id=%s AND representation=%s
                ORDER BY doc_id,chunk_index
                """).format(self.table),
                (strategy, model_id, representation),
            ).fetchall()
        return [
            SearchResult(
                str(r[0]), int(r[1]), str(r[2]), int(r[3]), int(r[4]), 0.0, cast(str | None, r[5])
            )
            for r in rows
        ]
