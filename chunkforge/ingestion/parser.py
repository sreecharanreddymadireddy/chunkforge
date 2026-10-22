"""Conservative PDF-to-clean-text parsing with provenance and batch statistics.

The algorithm extracts positioned text blocks, detects common two-column layouts,
removes repeated header/footer lines, drops standalone page numbers, repairs clear
line-break hyphenation, and records page-to-character spans. It preserves ambiguous
table text rather than inventing structure that the PDF does not contain.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import pymupdf

from chunkforge.config import get_settings
from chunkforge.ingestion.ocr import OcrOptions, needs_ocr, recognize_page

PARSER_VERSION = "1.1"
MARGIN_FRACTION = 0.12
REPEATED_MARGIN_THRESHOLD = 0.60
PAGE_NUMBER_PATTERN = re.compile(r"^(?:page\s*)?\d+(?:\s*(?:of|/)\s*\d+)?$", re.IGNORECASE)
DIGIT_PATTERN = re.compile(r"\d+")
WHITESPACE_PATTERN = re.compile(r"[ \t]+")
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
LOWERCASE_WORD_PATTERN = re.compile(r"^[a-z][A-Za-z]*")


@dataclass(frozen=True, slots=True)
class TextBlock:
    """One positioned text block extracted from a PDF page."""

    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def width(self) -> float:
        """Return the horizontal block extent."""

        return self.x1 - self.x0


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    """Raw positioned text plus layout classification for one page."""

    page_number: int
    width: float
    height: float
    blocks: tuple[TextBlock, ...]
    two_column: bool
    extraction_method: Literal["native", "ocr"] = "native"
    native_characters: int = 0


@dataclass(frozen=True, slots=True)
class PageSpan:
    """Map a PDF page to its character range in the clean document."""

    page_number: int
    char_start: int
    char_end: int
    two_column: bool
    extraction_method: Literal["native", "ocr"] = "native"
    native_characters: int = 0


@dataclass(frozen=True, slots=True)
class DocumentStatistics:
    """Observable cleaning decisions for one parsed PDF."""

    page_count: int
    raw_character_count: int
    clean_character_count: int
    empty_page_count: int
    two_column_page_count: int
    repeated_margin_lines_removed: int
    page_numbers_removed: int
    dehyphenations: int
    ocr_page_count: int = 0


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Clean text, page provenance, and measurable parser behavior."""

    source_pdf: str
    source_sha256: str
    parser_version: str
    text: str
    page_spans: tuple[PageSpan, ...]
    statistics: DocumentStatistics
    ocr_options: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BatchStatistics:
    """Aggregate results for a directory of PDFs."""

    discovered_documents: int
    parsed_documents: int
    skipped_documents: int
    failed_documents: int
    total_pages: int
    total_clean_characters: int
    average_characters_per_document: float
    total_empty_pages: int
    total_two_column_pages: int
    failures: tuple[dict[str, str], ...]


@dataclass(slots=True)
class _CleaningCounters:
    """Mutable counters used only while constructing an immutable result."""

    repeated_margin_lines_removed: int = 0
    page_numbers_removed: int = 0
    dehyphenations: int = 0


def _sha256(path: Path) -> str:
    """Return a streaming SHA-256 digest for source provenance."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_unicode(value: str) -> str:
    """Normalize compatibility characters and remove unsafe control bytes."""

    normalized = unicodedata.normalize("NFKC", value).replace("\u00ad", "")
    return CONTROL_PATTERN.sub("", normalized)


def _canonical_margin_line(value: str) -> str:
    """Canonicalize a margin line so changing page digits still match."""

    normalized = WHITESPACE_PATTERN.sub(" ", _normalize_unicode(value)).strip().casefold()
    return DIGIT_PATTERN.sub("#", normalized)


def _extract_blocks(
    page: pymupdf.Page, textpage: pymupdf.TextPage | None = None
) -> list[TextBlock]:
    """Extract text blocks while filtering images and empty content."""

    raw_blocks = cast(
        list[tuple[Any, ...]],
        page.get_text("blocks", sort=True, textpage=textpage),  # type: ignore[no-untyped-call]
    )
    blocks: list[TextBlock] = []
    for raw in raw_blocks:
        if len(raw) < 5 or (len(raw) > 6 and raw[6] != 0):
            continue
        text = _normalize_unicode(str(raw[4])).strip()
        if not text:
            continue
        blocks.append(
            TextBlock(
                x0=float(raw[0]),
                y0=float(raw[1]),
                x1=float(raw[2]),
                y1=float(raw[3]),
                text=text,
            )
        )
    return blocks


def _is_two_column(blocks: Sequence[TextBlock], page_width: float) -> bool:
    """Detect a two-column body from multiple narrow blocks on both sides."""

    left = [block for block in blocks if block.x1 <= page_width * 0.58]
    right = [block for block in blocks if block.x0 >= page_width * 0.42]
    return len(left) >= 2 and len(right) >= 2


def _order_blocks(blocks: Sequence[TextBlock], page_width: float) -> tuple[TextBlock, ...]:
    """Order a page top-to-bottom, reading the left column before the right.

    1. Detect at least two narrow blocks on each side of the page.
    2. Keep full-width title/header blocks above the columns first.
    3. Read all left-column blocks from top to bottom.
    4. Read all right-column blocks from top to bottom.
    5. Append spanning blocks below the column body.
    """

    default_order = tuple(sorted(blocks, key=lambda block: (block.y0, block.x0)))
    if not _is_two_column(blocks, page_width):
        return default_order

    left = [block for block in blocks if block.x1 <= page_width * 0.58]
    right = [block for block in blocks if block.x0 >= page_width * 0.42]
    columns = left + right
    column_top = min(block.y0 for block in columns)
    column_bottom = max(block.y1 for block in columns)
    column_ids = {id(block) for block in columns}
    spanning = [block for block in blocks if id(block) not in column_ids]
    above = [block for block in spanning if block.y1 <= column_top + 5]
    below = [block for block in spanning if block.y0 >= column_bottom - 5]
    middle = [block for block in spanning if block not in above and block not in below]

    return tuple(
        sorted(above, key=lambda block: (block.y0, block.x0))
        + sorted(left, key=lambda block: (block.y0, block.x0))
        + sorted(right, key=lambda block: (block.y0, block.x0))
        + sorted(middle + below, key=lambda block: (block.y0, block.x0))
    )


def _extract_pages(document: pymupdf.Document, ocr: OcrOptions) -> tuple[ExtractedPage, ...]:
    """Extract and classify every page before cross-page cleanup."""

    pages: list[ExtractedPage] = []
    ocr_pages = 0
    for index in range(document.page_count):
        page: pymupdf.Page = document.load_page(index)  # type: ignore[no-untyped-call]
        blocks = _extract_blocks(page)
        native_text = "\n".join(block.text for block in blocks)
        method: Literal["native", "ocr"] = "native"
        if needs_ocr(page, native_text, ocr):
            ocr_pages += 1
            if ocr_pages > ocr.max_pages:
                raise RuntimeError("Document exceeds configured OCR page budget")
            blocks = _extract_blocks(page, recognize_page(page, ocr))
            method = "ocr"
        width = float(page.rect.width)
        height = float(page.rect.height)
        pages.append(
            ExtractedPage(
                page_number=index + 1,
                width=width,
                height=height,
                blocks=_order_blocks(blocks, width),
                two_column=_is_two_column(blocks, width),
                extraction_method=method,
                native_characters=sum(c.isalnum() for c in native_text),
            )
        )
    return tuple(pages)


def _line_is_in_margin(block: TextBlock, page_height: float) -> bool:
    """Return whether a block intersects the top or bottom margin band."""

    return block.y0 <= page_height * MARGIN_FRACTION or block.y1 >= page_height * (
        1 - MARGIN_FRACTION
    )


def _find_repeated_margin_lines(pages: Sequence[ExtractedPage]) -> frozenset[str]:
    """Find normalized top/bottom lines repeated on most pages."""

    if len(pages) < 2:
        return frozenset()
    occurrences: Counter[str] = Counter()
    for page in pages:
        page_candidates: set[str] = set()
        for block in page.blocks:
            if not _line_is_in_margin(block, page.height):
                continue
            for line in block.text.splitlines():
                candidate = _canonical_margin_line(line)
                if 3 <= len(candidate) <= 160:
                    page_candidates.add(candidate)
        occurrences.update(page_candidates)

    minimum_pages = max(2, math.ceil(len(pages) * REPEATED_MARGIN_THRESHOLD))
    return frozenset(
        candidate for candidate, count in occurrences.items() if count >= minimum_pages
    )


def _clean_lines(lines: Sequence[str], counters: _CleaningCounters) -> str:
    """Normalize one block while preserving intentional paragraph breaks."""

    paragraphs: list[str] = []
    current = ""
    for raw_line in lines:
        line = WHITESPACE_PATTERN.sub(" ", _normalize_unicode(raw_line)).strip()
        if not line:
            if current:
                paragraphs.append(current)
                current = ""
            continue

        if current.endswith("-") and LOWERCASE_WORD_PATTERN.match(line):
            current = f"{current[:-1]}{line}"
            counters.dehyphenations += 1
        elif current:
            current = f"{current} {line}"
        else:
            current = line
    if current:
        paragraphs.append(current)
    return "\n\n".join(paragraphs)


def _clean_page(
    page: ExtractedPage,
    repeated_margin_lines: frozenset[str],
    counters: _CleaningCounters,
) -> str:
    """Remove confirmed margin noise and clean each text block."""

    clean_blocks: list[str] = []
    for block in page.blocks:
        retained_lines: list[str] = []
        in_margin = _line_is_in_margin(block, page.height)
        for line in block.text.splitlines():
            stripped = WHITESPACE_PATTERN.sub(" ", line).strip()
            canonical = _canonical_margin_line(stripped)
            if in_margin and PAGE_NUMBER_PATTERN.fullmatch(stripped):
                counters.page_numbers_removed += 1
                continue
            if in_margin and canonical in repeated_margin_lines:
                counters.repeated_margin_lines_removed += 1
                continue
            retained_lines.append(stripped)
        clean_block = _clean_lines(retained_lines, counters)
        if clean_block:
            clean_blocks.append(clean_block)
    return "\n\n".join(clean_blocks).strip()


def parse_pdf(pdf_path: Path, *, ocr: OcrOptions | None = None) -> ParsedDocument:
    """Parse one PDF into normalized text and page-level character provenance."""

    options = ocr or OcrOptions()
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    with pymupdf.open(pdf_path) as document:  # type: ignore[no-untyped-call]
        if document.needs_pass:
            raise ValueError(f"encrypted PDF requires a password: {pdf_path.name}")
        pages = _extract_pages(document, options)

    repeated_margin_lines = _find_repeated_margin_lines(pages)
    counters = _CleaningCounters()
    clean_pages = [_clean_page(page, repeated_margin_lines, counters) for page in pages]

    text_parts: list[str] = []
    spans: list[PageSpan] = []
    cursor = 0
    for page, page_text in zip(pages, clean_pages, strict=True):
        if text_parts:
            separator = "\n\n"
            text_parts.append(separator)
            cursor += len(separator)
        start = cursor
        text_parts.append(page_text)
        cursor += len(page_text)
        spans.append(
            PageSpan(
                page_number=page.page_number,
                char_start=start,
                char_end=cursor,
                two_column=page.two_column,
                extraction_method=page.extraction_method,
                native_characters=page.native_characters,
            )
        )

    # Keep separators around empty pages: stripping after recording spans corrupts offsets.
    text = "".join(text_parts)
    statistics = DocumentStatistics(
        page_count=len(pages),
        raw_character_count=sum(len(block.text) for page in pages for block in page.blocks),
        clean_character_count=len(text),
        empty_page_count=sum(not page_text for page_text in clean_pages),
        two_column_page_count=sum(page.two_column for page in pages),
        repeated_margin_lines_removed=counters.repeated_margin_lines_removed,
        page_numbers_removed=counters.page_numbers_removed,
        dehyphenations=counters.dehyphenations,
        ocr_page_count=sum(page.extraction_method == "ocr" for page in pages),
    )
    return ParsedDocument(
        source_pdf=pdf_path.as_posix(),
        source_sha256=_sha256(pdf_path),
        parser_version=PARSER_VERSION,
        text=text,
        page_spans=tuple(spans),
        statistics=statistics,
        ocr_options=options.signature(),
    )


def _atomic_write(path: Path, content: str) -> None:
    """Replace a text file only after its complete content reaches disk."""

    temporary = path.with_suffix(f"{path.suffix}.part")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _write_parsed_document(document: ParsedDocument, processed_dir: Path, stem: str) -> None:
    """Write clean text and a separate provenance/statistics sidecar."""

    text_path = processed_dir / f"{stem}.txt"
    metadata_path = processed_dir / f"{stem}.json"
    metadata = {
        "source_pdf": document.source_pdf,
        "source_sha256": document.source_sha256,
        "parser_version": document.parser_version,
        "ocr_options": document.ocr_options,
        "clean_text_sha256": hashlib.sha256((document.text + "\n").encode()).hexdigest(),
        "page_spans": [asdict(span) for span in document.page_spans],
        "statistics": asdict(document.statistics),
    }
    _atomic_write(text_path, f"{document.text}\n")
    _atomic_write(metadata_path, f"{json.dumps(metadata, indent=2, sort_keys=True)}\n")


def parse_corpus(
    raw_dir: Path,
    processed_dir: Path,
    *,
    overwrite: bool = False,
    ocr: OcrOptions | None = None,
) -> BatchStatistics:
    """Parse every raw PDF and print measurable corpus-quality statistics."""

    if not raw_dir.is_dir():
        raise FileNotFoundError(f"raw PDF directory does not exist: {raw_dir}")
    options = ocr or OcrOptions()
    processed_dir.mkdir(parents=True, exist_ok=True)
    pdf_paths = sorted(raw_dir.glob("*.pdf"))
    parsed = 0
    skipped = 0
    failures: list[dict[str, str]] = []
    total_pages = 0
    total_characters = 0
    total_empty_pages = 0
    total_two_column_pages = 0

    for index, pdf_path in enumerate(pdf_paths, start=1):
        text_path = processed_dir / f"{pdf_path.stem}.txt"
        metadata_path = processed_dir / f"{pdf_path.stem}.json"
        reusable = False
        existing_metadata: dict[str, Any] = {}
        existing_text = ""
        if (
            not overwrite
            and text_path.is_file()
            and metadata_path.is_file()
            and text_path.stat().st_mtime >= pdf_path.stat().st_mtime
        ):
            try:
                existing_text = text_path.read_text(encoding="utf-8")
                existing_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                reusable = (
                    existing_metadata.get("parser_version") == PARSER_VERSION
                    and existing_metadata.get("ocr_options") == options.signature()
                    and existing_metadata.get("source_sha256") == _sha256(pdf_path)
                    and existing_metadata.get("clean_text_sha256")
                    == hashlib.sha256(existing_text.encode()).hexdigest()
                    and bool(existing_text.strip())
                )
            except (OSError, ValueError, AttributeError):
                reusable = False
        if reusable:
            skipped += 1
            existing_stats = existing_metadata["statistics"]
            total_characters += int(existing_stats["clean_character_count"])
            total_pages += int(existing_stats["page_count"])
            total_empty_pages += int(existing_stats["empty_page_count"])
            total_two_column_pages += int(existing_stats["two_column_page_count"])
            print(f"[{index}/{len(pdf_paths)}] skip {pdf_path.name}")
            continue

        try:
            document = parse_pdf(pdf_path, ocr=options)
            if not document.text.strip():
                raise ValueError("no usable text; enable OCR for scans or inspect blank pages")
            _write_parsed_document(document, processed_dir, pdf_path.stem)
        except (OSError, RuntimeError, ValueError, pymupdf.FileDataError) as exc:
            failures.append({"pdf": pdf_path.name, "error": str(exc)})
            print(f"[{index}/{len(pdf_paths)}] failed {pdf_path.name}: {exc}")
            continue

        parsed += 1
        stats = document.statistics
        total_characters += stats.clean_character_count
        total_pages += stats.page_count
        total_empty_pages += stats.empty_page_count
        total_two_column_pages += stats.two_column_page_count
        print(
            f"[{index}/{len(pdf_paths)}] parsed {pdf_path.name}: "
            f"pages={stats.page_count}, chars={stats.clean_character_count:,}, "
            f"ocr_pages={stats.ocr_page_count}"
        )

    completed_documents = parsed + skipped
    average_characters = total_characters / completed_documents if completed_documents else 0.0
    summary = BatchStatistics(
        discovered_documents=len(pdf_paths),
        parsed_documents=parsed,
        skipped_documents=skipped,
        failed_documents=len(failures),
        total_pages=total_pages,
        total_clean_characters=total_characters,
        average_characters_per_document=average_characters,
        total_empty_pages=total_empty_pages,
        total_two_column_pages=total_two_column_pages,
        failures=tuple(failures),
    )
    _atomic_write(
        processed_dir / "ingestion_stats.json",
        f"{json.dumps(asdict(summary), indent=2, sort_keys=True)}\n",
    )
    return summary


def write_parsed_document(document: ParsedDocument, processed_dir: Path, stem: str) -> None:
    """Public single-document output contract for event-driven ingestion."""
    processed_dir.mkdir(parents=True, exist_ok=True)
    _write_parsed_document(document, processed_dir, stem)


def build_argument_parser() -> argparse.ArgumentParser:
    """Construct the batch parser command-line contract."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=None)
    parser.add_argument("--processed-dir", type=Path, default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--ocr-mode", choices=["off", "auto", "force"], default=None)
    parser.add_argument("--ocr-language", default=None)
    parser.add_argument("--ocr-dpi", type=int, default=None)
    parser.add_argument("--tessdata", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run batch parsing and print the Phase 1 acceptance statistics."""

    arguments = build_argument_parser().parse_args(argv)
    settings = get_settings()
    raw_dir = arguments.raw_dir or settings.raw_data_dir
    processed_dir = arguments.processed_dir or settings.processed_data_dir
    overrides = {
        key: value
        for key, value in {
            "mode": arguments.ocr_mode,
            "language": arguments.ocr_language,
            "dpi": arguments.ocr_dpi,
            "tessdata": arguments.tessdata,
        }.items()
        if value is not None
    }
    options = OcrOptions.model_validate(
        {**OcrOptions.from_settings(settings).model_dump(), **overrides}
    )
    summary = parse_corpus(raw_dir, processed_dir, overwrite=arguments.overwrite, ocr=options)
    print(
        "Parsing summary: "
        f"discovered={summary.discovered_documents}, parsed={summary.parsed_documents}, "
        f"skipped={summary.skipped_documents}, failed={summary.failed_documents}, "
        f"pages={summary.total_pages}, "
        f"avg_chars_per_doc={summary.average_characters_per_document:,.1f}, "
        f"empty_pages={summary.total_empty_pages}, "
        f"two_column_pages={summary.total_two_column_pages}"
    )
    return 1 if summary.failed_documents else 0


if __name__ == "__main__":
    raise SystemExit(main())
