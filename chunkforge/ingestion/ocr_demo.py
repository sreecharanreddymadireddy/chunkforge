"""Generate five synthetic OCR acceptance PDFs and optionally index an isolated demo table.

No downloaded papers or default corpus tables are modified. The generated sources
are test documents, not external research papers or an OCR accuracy benchmark.
"""

# mypy: disable-error-code=no-untyped-call
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import pymupdf

from chunkforge.ingestion.ocr import OcrOptions
from chunkforge.ingestion.parser import parse_corpus, parse_pdf

DEMO_TABLE = "chunkforge_ocr_demo"
CASES: dict[str, list[tuple[str, str]]] = {
    "01_digital": [
        (
            "native",
            "Native extraction preserves searchable source text without optical recognition.",
        )
    ],
    "02_scanned": [
        ("scan", "Optical recognition recovers scanned delivery instructions for the support team.")
    ],
    "03_mixed": [
        (
            "native",
            "Digital API documentation describes the ingestion contract and source identity.",
        ),
        ("scan", "Kafka consumers commit offsets after database writes succeed."),
    ],
    "04_sparse_native": [
        (
            "sparse",
            "Circuit breakers prevent repeated calls to an unavailable embedding dependency.",
        )
    ],
    "05_scan_blank_scan": [
        ("scan", "Redis caches query embeddings, not final answers."),
        ("blank", ""),
        ("scan", "Source citations preserve document identity and character ranges."),
    ],
}


def _text_page(document: pymupdf.Document, text: str) -> pymupdf.Page:
    """Use high-contrast vector text to construct a controlled raster scan fixture."""
    page = document.new_page(width=600, height=800)
    remaining = page.insert_textbox(pymupdf.Rect(50, 150, 550, 600), text, fontsize=20)
    if remaining < 0:
        raise ValueError("Synthetic OCR fixture text does not fit the page")
    return page


def create_fixtures(raw_dir: Path) -> list[Path]:
    """Create missing fixture PDFs only; never overwrite an existing input file."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    expected_names = {name + ".pdf" for name in CASES}
    if any(p.name not in expected_names for p in raw_dir.glob("*.pdf")):
        raise ValueError("OCR demo directory contains unrelated PDFs; choose an isolated root")
    paths: list[Path] = []
    for name, pages in CASES.items():
        path = raw_dir / (name + ".pdf")
        if path.exists():
            with pymupdf.open(path) as existing:
                if existing.metadata.get("subject") != "ChunkForge synthetic OCR fixture":
                    raise ValueError(
                        "Refusing to reuse an input that is not a synthetic OCR fixture"
                    )
        if not path.exists():
            with pymupdf.open() as output:
                for method, text in pages:
                    if method == "native":
                        _text_page(output, text)
                    elif method == "blank":
                        output.new_page(width=600, height=800)
                    else:
                        with pymupdf.open() as digital:
                            page = _text_page(digital, text)
                            image = page.get_pixmap(dpi=150, alpha=False).tobytes("png")
                        scan = output.new_page(width=600, height=800)
                        scan.insert_image(scan.rect, stream=image)
                        if method == "sparse":
                            scan.insert_text((50, 60), "SCAN", fontsize=12)
                output.set_metadata({"title": name, "subject": "ChunkForge synthetic OCR fixture"})
                output.save(path)
        paths.append(path)
    return paths


def verify_fixtures(root: Path, options: OcrOptions) -> dict[str, object]:
    """Check actual recognition, source spans and cached batch restartability."""
    paths = create_fixtures(root / "raw")
    rows: list[dict[str, object]] = []
    for path in paths:
        baseline = parse_pdf(path)
        parsed = parse_pdf(path, ocr=options)
        expected = CASES[path.stem]
        for span, (method, text) in zip(parsed.page_spans, expected, strict=True):
            actual = parsed.text[span.char_start : span.char_end]
            expected_method = "ocr" if method in ("scan", "sparse") else "native"
            if span.extraction_method != expected_method:
                raise AssertionError(f"Wrong extraction route for {path.stem}/{span.page_number}")
            if method != "blank" and " ".join(text.casefold().split()) not in " ".join(
                actual.casefold().split()
            ):
                raise AssertionError(
                    f"OCR fixture evidence missing: {path.stem}/{span.page_number}"
                )
            if method == "blank" and actual:
                raise AssertionError("Blank page invented text")
        rows.append(
            {
                "document": path.stem,
                "native_only_characters": len(baseline.text),
                "ocr_enabled_characters": len(parsed.text),
                "ocr_pages": parsed.statistics.ocr_page_count,
                "page_spans": [asdict(span) for span in parsed.page_spans],
                "text": parsed.text,
            }
        )
    first = parse_corpus(root / "raw", root / "processed", ocr=options)
    second = parse_corpus(root / "raw", root / "processed", ocr=options)
    if first.failed_documents or second.skipped_documents != 5:
        raise AssertionError("OCR acceptance corpus failed or did not resume unchanged work")
    return {
        "scope": "five synthetic PDFs; real Tesseract recognition, not an accuracy benchmark",
        "documents": rows,
        "batch_first": asdict(first),
        "batch_repeat": asdict(second),
    }


def index_fixtures(root: Path) -> dict[str, object]:
    """Use real BGE and pgvector in a dedicated, retained five-document demo table."""
    from chunkforge.chunking.model_backend import BGEBackend
    from chunkforge.config import get_settings
    from chunkforge.embeddings.embedder import Embedder
    from chunkforge.embeddings.indexer import DocumentIndexer
    from chunkforge.store.pgvector_store import STRATEGIES, PgvectorStore

    settings = get_settings()
    corpus = PgvectorStore(str(settings.postgres_dsn), settings.embedding_dimensions)
    before = corpus.counts()
    backend = BGEBackend(settings.embedding_model, settings.embedding_batch_size, "cpu")
    revision = backend.transformer.auto_model.config._commit_hash
    if not revision:
        raise ValueError("Resolved model revision required")
    embedder = Embedder(
        backend, settings.embedding_model + "@" + str(revision), settings.embedding_dimensions
    )
    store = PgvectorStore(str(settings.postgres_dsn), settings.embedding_dimensions, DEMO_TABLE)
    store.create_schema()
    indexer = DocumentIndexer(settings, backend, embedder, store)
    outcomes: dict[str, object] = {}
    for path in [root / "processed" / (name + ".txt") for name in sorted(CASES)]:
        outcomes[path.stem] = [
            asdict(result)
            for result in indexer.index(path.read_text(), path.stem, list(STRATEGIES))
        ]
    initial = store.counts()
    for path in [root / "processed" / (name + ".txt") for name in sorted(CASES)]:
        if any(
            r.status != "unchanged"
            for r in indexer.index(path.read_text(), path.stem, list(STRATEGIES))
        ):
            raise AssertionError("Repeated OCR indexing did not skip unchanged work")
    if initial != store.counts() or before != corpus.counts():
        raise AssertionError("Replay changed counts or the main corpus was modified")
    question = "When should Kafka consumers commit offsets?"
    hits = store.search(
        embedder.embed_query(question, "recursive"),
        "recursive",
        embedder.model_id,
        embedder.representation("recursive"),
        limit=5,
    )
    if not hits or hits[0].doc_id != "03_mixed":
        raise AssertionError("Scanned source was not the first real vector search result")
    return {
        "table": DEMO_TABLE,
        "outcomes": outcomes,
        "stored_chunks": initial,
        "repeat": "unchanged across all five strategies",
        "query": question,
        "recursive_hits": [asdict(hit) for hit in hits],
        "main_corpus_counts_before_and_after": before,
    }


def main() -> None:
    """Run the isolated OCR acceptance lab; model/database work requires --index."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(".learning/ocr"))
    parser.add_argument("--tessdata", type=Path, default=None)
    parser.add_argument("--index", action="store_true")
    args = parser.parse_args()
    report = verify_fixtures(args.root, OcrOptions(mode="auto", tessdata=args.tessdata))
    if args.index:
        report["indexing"] = index_fixtures(args.root)
    (args.root / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
