"""Reproducible five-document chunk statistics; these are not retrieval scores."""

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

from chunkforge.chunking.base import Chunker, TokenCounter
from chunkforge.chunking.fixed import FixedChunker
from chunkforge.chunking.late import LateChunker
from chunkforge.chunking.model_backend import BGEBackend
from chunkforge.chunking.recursive import RecursiveChunker
from chunkforge.chunking.semantic import SemanticChunker
from chunkforge.chunking.sentence_window import SentenceWindowChunker
from chunkforge.config import get_settings


def main() -> None:
    """Compare full documents, validate provenance, and write measured Markdown/JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=int, default=5)
    parser.add_argument("--output", type=Path, default=Path("eval/CHUNKING_RESULTS.md"))
    args = parser.parse_args()
    if args.documents < 1:
        parser.error("--documents must be positive")
    settings = get_settings()
    paths = sorted(settings.processed_data_dir.glob("*.txt"))[: args.documents]
    if len(paths) != args.documents:
        parser.error(f"Need {args.documents} processed text files; found {len(paths)}")
    counter = TokenCounter(settings.token_encoding)
    backend = BGEBackend(
        settings.embedding_model,
        settings.embedding_batch_size,
        settings.embedding_device,
    )
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
    rows: list[dict[str, str | int | float]] = []
    documents = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        documents.append(
            {
                "doc_id": path.stem,
                "characters": len(text),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
        for name, chunker in strategies.items():
            started = perf_counter()
            chunks = chunker.chunk(text, path.stem)
            elapsed = perf_counter() - started
            covered_end = 0
            limit = settings.late_chunk_size if name == "late" else settings.chunk_size
            for i, chunk in enumerate(chunks):
                if (
                    chunk.chunk_index != i
                    or chunk.doc_id != path.stem
                    or chunk.text != text[chunk.char_start : chunk.char_end]
                    or chunk.char_start > covered_end
                    or chunk.token_count != counter.count(chunk.text)
                    or not 0 < chunk.token_count <= limit
                ):
                    raise ValueError(f"Invalid chunk provenance or budget: {path.name}/{name}/{i}")
                covered_end = max(covered_end, chunk.char_end)
            if not chunks or covered_end != len(text):
                raise ValueError(f"Incomplete document coverage: {path.name}/{name}")
            row: dict[str, str | int | float] = {
                "document": path.stem,
                "strategy": name,
                "chunks": len(chunks),
                "avg_tokens": round(sum(c.token_count for c in chunks) / len(chunks), 2),
                "max_tokens": max(c.token_count for c in chunks),
                "seconds": round(elapsed, 3),
            }
            rows.append(row)
            print(json.dumps(row), flush=True)
    lines = [
        "# Chunking comparison",
        "",
        "Measured on full cleaned source documents; every source span and token budget validated.",
        "These are chunk statistics, not retrieval-quality measurements or a strategy ranking.",
        "",
        f"Model: `{settings.embedding_model}`; device: `{settings.embedding_device}`; "
        f"model input cap: {backend.model.max_seq_length}; encoding: `{settings.token_encoding}`.",
        f"Core budget: {settings.chunk_size}; fixed overlap: {settings.chunk_overlap}; "
        f"semantic threshold: {settings.semantic_threshold}; "
        f"sentence window: ±{settings.sentence_window_size}; "
        f"late core: {settings.late_chunk_size}.",
        "",
        "Late uses mean pooling inside separate BGE-sized passages, not whole-paper context. "
        "Its smaller core budget makes counts unsuitable for a same-size quality comparison.",
        "Sentence-window counts describe indexed anchors; surrounding context can be larger.",
        "Times include tokenization/inference and vary by hardware; model loading is excluded.",
        "",
        "| Document | Strategy | Chunks | Avg core tokens | Max core tokens | Seconds |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['document']} | {row['strategy']} | {row['chunks']} | "
            f"{row['avg_tokens']} | {row['max_tokens']} | {row['seconds']} |",
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "Fixed windows are a size baseline; overlap repeats text. Recursive packing trades "
            "density for structural boundaries. Semantic counts depend on similarity thresholds "
            "and sentence segmentation. Sentence anchors store their context separately. "
            "Late vectors retain only bounded passage context. Retrieval evaluation must determine "
            "whether any of these trade-offs improve answers.",
            "",
            "Reproduce with `make chunk-compare`; source hashes are in the adjacent JSON report.",
            "",
        ]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    payload = {
        "model": settings.embedding_model,
        "model_revision": getattr(backend.transformer.auto_model.config, "_commit_hash", None),
        "model_max_seq_length": backend.model.max_seq_length,
        "configuration": settings.model_dump(
            include={
                "token_encoding",
                "chunk_size",
                "chunk_overlap",
                "semantic_threshold",
                "sentence_window_size",
                "late_chunk_size",
                "embedding_batch_size",
                "embedding_device",
            }
        ),
        "documents": documents,
        "results": rows,
    }
    args.output.with_suffix(".json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Saved {args.output}", flush=True)


if __name__ == "__main__":
    main()
