"""Tests for Phase 1 downloading, PDF cleaning, provenance, and restartability."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pymupdf
import pytest

from chunkforge.ingestion.downloader import (
    ArxivPaper,
    RequestRateLimiter,
    download_papers,
    fetch_papers,
    parse_atom_feed,
)
from chunkforge.ingestion.parser import parse_corpus, parse_pdf

ATOM_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>https://arxiv.org/abs/2501.01234v1</id>
    <updated>2025-01-03T00:00:00Z</updated>
    <published>2025-01-02T00:00:00Z</published>
    <title>  A   Reliable\n Retrieval System  </title>
    <summary>Measured retrieval quality for production RAG.</summary>
    <author><name>Ada Engineer</name></author>
    <author><name>Grace Researcher</name></author>
    <category term="cs.LG"/>
    <category term="cs.IR"/>
    <link href="http://arxiv.org/pdf/2501.01234v1" title="pdf" type="application/pdf"/>
    <arxiv:license>https://creativecommons.org/licenses/by/4.0/</arxiv:license>
  </entry>
</feed>
"""


def _paper() -> ArxivPaper:
    """Return stable metadata for isolated download tests."""

    return ArxivPaper(
        arxiv_id="2501.01234v1",
        title="A Reliable Retrieval System",
        abstract="Measured retrieval quality for production RAG.",
        authors=("Ada Engineer",),
        categories=("cs.LG",),
        published="2025-01-02T00:00:00Z",
        updated="2025-01-03T00:00:00Z",
        pdf_url="https://arxiv.org/pdf/2501.01234v1",
        license_url="https://creativecommons.org/licenses/by/4.0/",
    )


def _create_repeated_margin_pdf(path: Path) -> None:
    """Create a deterministic PDF with headers, page numbers, and hyphenation."""

    document = pymupdf.open()  # type: ignore[no-untyped-call]
    for page_number in range(1, 4):
        page = document.new_page(width=600, height=800)
        page.insert_text((50, 40), "ChunkForge Research Paper", fontsize=10)
        page.insert_textbox(
            (50, 110, 550, 350),
            (
                f"Section {page_number}\nA transfor-\n"
                f"mer retrieves useful evidence on page {page_number}."
            ),
            fontsize=12,
        )
        page.insert_text((275, 775), f"Page {page_number}", fontsize=10)
    document.save(path)  # type: ignore[no-untyped-call]
    document.close()  # type: ignore[no-untyped-call]


def _create_two_column_pdf(path: Path) -> None:
    """Create two blocks per column so the layout detector has strong evidence."""

    document = pymupdf.open()  # type: ignore[no-untyped-call]
    page = document.new_page(width=600, height=800)
    page.insert_textbox((40, 80, 560, 120), "Two Column Study", fontsize=14)
    page.insert_textbox((40, 150, 270, 240), "Left column first block.", fontsize=11)
    page.insert_textbox((40, 260, 270, 350), "Left column second block.", fontsize=11)
    page.insert_textbox((330, 150, 560, 240), "Right column first block.", fontsize=11)
    page.insert_textbox((330, 260, 560, 350), "Right column second block.", fontsize=11)
    document.save(path)  # type: ignore[no-untyped-call]
    document.close()  # type: ignore[no-untyped-call]


def test_parse_atom_feed_extracts_typed_metadata() -> None:
    """Atom namespaces, whitespace, authors, categories, and HTTPS links are normalized."""

    papers = parse_atom_feed(ATOM_FEED)

    assert len(papers) == 1
    assert papers[0].arxiv_id == "2501.01234v1"
    assert papers[0].title == "A Reliable Retrieval System"
    assert papers[0].authors == ("Ada Engineer", "Grace Researcher")
    assert papers[0].categories == ("cs.LG", "cs.IR")
    assert papers[0].pdf_url == "https://arxiv.org/pdf/2501.01234v1"


def test_rate_limiter_waits_for_remaining_interval() -> None:
    """Sequential requests should be separated without sleeping before the first."""

    now = [10.0]
    sleeps: list[float] = []

    def clock() -> float:
        return now[0]

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = RequestRateLimiter(3.0, clock=clock, sleeper=sleeper)
    limiter.wait()
    now[0] += 1.25
    limiter.wait()

    assert sleeps == [pytest.approx(1.75)]


def test_fetch_and_resumable_download_with_mock_transport(tmp_path: Path) -> None:
    """Downloader should query Atom once, validate PDFs, and skip completed files."""

    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.host == "export.arxiv.org":
            return httpx.Response(200, content=ATOM_FEED, request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.7\nsynthetic test payload\n%%EOF",
            request=request,
        )

    transport = httpx.MockTransport(handler)
    limiter = RequestRateLimiter(0)
    with httpx.Client(transport=transport) as client:
        papers = fetch_papers(client, limiter, max_results=1)
        first = download_papers(client, limiter, papers, tmp_path)
        second = download_papers(client, limiter, papers, tmp_path)

    assert first.downloaded == 1
    assert first.failed == 0
    assert second.downloaded == 0
    assert second.skipped == 1
    assert (tmp_path / _paper().filename).read_bytes().startswith(b"%PDF-")
    manifest_record = json.loads((tmp_path / "manifest.jsonl").read_text().splitlines()[0])
    assert manifest_record["status"] == "skipped_existing"
    assert len(requested_urls) == 2


def test_parser_removes_repeated_margins_and_repairs_hyphenation(tmp_path: Path) -> None:
    """Repeated page furniture should not pollute the clean document."""

    pdf_path = tmp_path / "paper.pdf"
    _create_repeated_margin_pdf(pdf_path)

    document = parse_pdf(pdf_path)

    assert "ChunkForge Research Paper" not in document.text
    assert "Page 1" not in document.text
    assert "transformer retrieves" in document.text
    assert document.statistics.page_count == 3
    assert document.statistics.repeated_margin_lines_removed == 3
    assert document.statistics.page_numbers_removed == 3
    assert document.statistics.dehyphenations == 3
    assert len(document.page_spans) == 3
    assert document.page_spans[-1].char_end == len(document.text)


def test_parser_orders_two_columns_and_batch_is_resumable(tmp_path: Path) -> None:
    """Column order and processed sidecars should remain deterministic across reruns."""

    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    raw_dir.mkdir()
    pdf_path = raw_dir / "columns.pdf"
    _create_two_column_pdf(pdf_path)

    document = parse_pdf(pdf_path)
    first = parse_corpus(raw_dir, processed_dir)
    second = parse_corpus(raw_dir, processed_dir)

    assert document.statistics.two_column_page_count == 1
    assert document.text.index("Left column second") < document.text.index("Right column first")
    assert first.parsed_documents == 1
    assert first.average_characters_per_document > 0
    assert second.skipped_documents == 1
    assert (processed_dir / "columns.txt").is_file()
    assert (processed_dir / "columns.json").is_file()
    assert (processed_dir / "ingestion_stats.json").is_file()
