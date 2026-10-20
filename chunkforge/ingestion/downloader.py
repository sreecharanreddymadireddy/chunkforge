"""Rate-limited, resumable arXiv PDF acquisition.

The arXiv API returns an Atom XML feed containing paper metadata and PDF links.
This module performs one request at a time, enforces arXiv's three-second minimum
interval, validates PDF signatures, writes downloads atomically, and records a
JSON Lines manifest for reproducibility.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.etree import ElementTree

import httpx

from chunkforge.config import get_settings

ARXIV_API_URL = "https://export.arxiv.org/api/query"
DEFAULT_QUERY = "cat:cs.LG OR cat:stat.ML"
DEFAULT_MAX_RESULTS = 75
MIN_REQUEST_INTERVAL_SECONDS = 3.0
USER_AGENT = "ChunkForge/0.1 (educational RAG research project)"

ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"
ARXIV_NAMESPACE = "http://arxiv.org/schemas/atom"
NAMESPACES = {"atom": ATOM_NAMESPACE, "arxiv": ARXIV_NAMESPACE}


@dataclass(frozen=True, slots=True)
class ArxivPaper:
    """Metadata required to identify and reproduce one downloaded paper."""

    arxiv_id: str
    title: str
    abstract: str
    authors: tuple[str, ...]
    categories: tuple[str, ...]
    published: str
    updated: str
    pdf_url: str
    license_url: str | None

    @property
    def filename(self) -> str:
        """Return a filesystem-safe PDF filename derived from the arXiv ID."""

        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", self.arxiv_id)
        return f"{safe_id}.pdf"


@dataclass(frozen=True, slots=True)
class DownloadRecord:
    """Manifest entry describing one requested paper and local result."""

    paper: ArxivPaper
    status: str
    local_path: str
    byte_count: int
    sha256: str | None
    error: str | None = None

    def to_json(self) -> str:
        """Serialize the record as one stable JSON Lines entry."""

        return json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class DownloadSummary:
    """Aggregate result for one corpus acquisition run."""

    requested: int
    downloaded: int
    skipped: int
    failed: int
    downloaded_bytes: int
    manifest_path: Path


class RequestRateLimiter:
    """Enforce a minimum delay between sequential request start times."""

    def __init__(
        self,
        minimum_interval_seconds: float = MIN_REQUEST_INTERVAL_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if minimum_interval_seconds < 0:
            raise ValueError("minimum_interval_seconds cannot be negative")
        self._minimum_interval_seconds = minimum_interval_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._last_request_started_at: float | None = None

    def wait(self) -> None:
        """Sleep only as long as needed, then record the next request start."""

        now = self._clock()
        if self._last_request_started_at is not None:
            elapsed = now - self._last_request_started_at
            remaining = self._minimum_interval_seconds - elapsed
            if remaining > 0:
                self._sleeper(remaining)
                now = self._clock()
        self._last_request_started_at = now


def _normalized_text(value: str | None) -> str:
    """Collapse XML formatting whitespace into a readable single line."""

    return " ".join((value or "").split())


def _required_text(entry: ElementTree.Element, xpath: str, field_name: str) -> str:
    """Extract a required Atom element or raise a descriptive data error."""

    value = entry.findtext(xpath, namespaces=NAMESPACES)
    normalized = _normalized_text(value)
    if not normalized:
        raise ValueError(f"arXiv entry is missing required field: {field_name}")
    return normalized


def parse_atom_feed(payload: bytes) -> list[ArxivPaper]:
    """Parse arXiv's Atom response into typed paper metadata."""

    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise ValueError("arXiv returned malformed Atom XML") from exc

    papers: list[ArxivPaper] = []
    for entry in root.findall("atom:entry", NAMESPACES):
        entry_url = _required_text(entry, "atom:id", "id")
        marker = "/abs/"
        if marker not in entry_url:
            raise ValueError(f"unexpected arXiv entry identifier: {entry_url}")
        arxiv_id = entry_url.rsplit(marker, maxsplit=1)[1]

        pdf_url: str | None = None
        for link in entry.findall("atom:link", NAMESPACES):
            if link.attrib.get("type") == "application/pdf" or link.attrib.get("title") == "pdf":
                pdf_url = link.attrib.get("href")
                break
        if not pdf_url:
            raise ValueError(f"arXiv entry {arxiv_id} has no PDF link")

        authors = tuple(
            _normalized_text(author.findtext("atom:name", namespaces=NAMESPACES))
            for author in entry.findall("atom:author", NAMESPACES)
        )
        authors = tuple(author for author in authors if author)
        categories = tuple(
            category.attrib["term"]
            for category in entry.findall("atom:category", NAMESPACES)
            if category.attrib.get("term")
        )

        papers.append(
            ArxivPaper(
                arxiv_id=arxiv_id,
                title=_required_text(entry, "atom:title", "title"),
                abstract=_required_text(entry, "atom:summary", "summary"),
                authors=authors,
                categories=categories,
                published=_required_text(entry, "atom:published", "published"),
                updated=_required_text(entry, "atom:updated", "updated"),
                pdf_url=pdf_url.replace("http://", "https://", 1),
                license_url=_normalized_text(
                    entry.findtext("arxiv:license", namespaces=NAMESPACES)
                )
                or None,
            )
        )
    return papers


def fetch_papers(
    client: httpx.Client,
    limiter: RequestRateLimiter,
    *,
    query: str = DEFAULT_QUERY,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> list[ArxivPaper]:
    """Fetch one sorted arXiv metadata page and validate the result count."""

    if not 1 <= max_results <= 2_000:
        raise ValueError("max_results must be between 1 and 2000")
    limiter.wait()
    response = client.get(
        ARXIV_API_URL,
        params={
            "search_query": query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        },
    )
    response.raise_for_status()
    papers = parse_atom_feed(response.content)
    if len(papers) < max_results:
        raise RuntimeError(
            f"arXiv returned {len(papers)} papers, fewer than the requested {max_results}"
        )
    return papers[:max_results]


def _sha256(path: Path) -> str:
    """Return a streaming SHA-256 digest for a local file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_pdf(path: Path) -> bool:
    """Check the inexpensive PDF signature before trusting an existing file."""

    if not path.is_file() or path.stat().st_size < 5:
        return False
    with path.open("rb") as handle:
        return handle.read(5) == b"%PDF-"


def _download_pdf(
    client: httpx.Client,
    limiter: RequestRateLimiter,
    paper: ArxivPaper,
    destination: Path,
) -> tuple[int, str]:
    """Stream one PDF to an atomic temporary file and return size plus digest."""

    temporary = destination.with_suffix(f"{destination.suffix}.part")
    digest = hashlib.sha256()
    byte_count = 0
    limiter.wait()
    try:
        with client.stream("GET", paper.pdf_url) as response:
            response.raise_for_status()
            with temporary.open("wb") as handle:
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
                        digest.update(chunk)
                        byte_count += len(chunk)
        if not _is_pdf(temporary):
            raise ValueError("downloaded content does not start with the PDF signature")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return byte_count, digest.hexdigest()


def download_papers(
    client: httpx.Client,
    limiter: RequestRateLimiter,
    papers: Sequence[ArxivPaper],
    raw_dir: Path,
    *,
    overwrite: bool = False,
) -> DownloadSummary:
    """Download papers sequentially, resume valid files, and write a manifest."""

    raw_dir.mkdir(parents=True, exist_ok=True)
    records: list[DownloadRecord] = []
    downloaded = 0
    skipped = 0
    failed = 0
    downloaded_bytes = 0

    for index, paper in enumerate(papers, start=1):
        destination = raw_dir / paper.filename
        if not overwrite and _is_pdf(destination):
            skipped += 1
            records.append(
                DownloadRecord(
                    paper=paper,
                    status="skipped_existing",
                    local_path=destination.as_posix(),
                    byte_count=destination.stat().st_size,
                    sha256=_sha256(destination),
                )
            )
            print(f"[{index}/{len(papers)}] skip {paper.arxiv_id}")
            continue

        try:
            byte_count, digest = _download_pdf(client, limiter, paper, destination)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            failed += 1
            records.append(
                DownloadRecord(
                    paper=paper,
                    status="failed",
                    local_path=destination.as_posix(),
                    byte_count=0,
                    sha256=None,
                    error=str(exc),
                )
            )
            print(f"[{index}/{len(papers)}] failed {paper.arxiv_id}: {exc}")
            continue

        downloaded += 1
        downloaded_bytes += byte_count
        records.append(
            DownloadRecord(
                paper=paper,
                status="downloaded",
                local_path=destination.as_posix(),
                byte_count=byte_count,
                sha256=digest,
            )
        )
        print(f"[{index}/{len(papers)}] downloaded {paper.arxiv_id} ({byte_count:,} bytes)")

    manifest_path = raw_dir / "manifest.jsonl"
    temporary_manifest = manifest_path.with_suffix(".jsonl.part")
    manifest_content = "\n".join(record.to_json() for record in records)
    temporary_manifest.write_text(f"{manifest_content}\n", encoding="utf-8")
    temporary_manifest.replace(manifest_path)

    return DownloadSummary(
        requested=len(papers),
        downloaded=downloaded,
        skipped=skipped,
        failed=failed,
        downloaded_bytes=downloaded_bytes,
        manifest_path=manifest_path,
    )


def download_corpus(
    raw_dir: Path,
    *,
    query: str = DEFAULT_QUERY,
    max_results: int = DEFAULT_MAX_RESULTS,
    request_delay_seconds: float = MIN_REQUEST_INTERVAL_SECONDS,
    timeout_seconds: float = 60.0,
    overwrite: bool = False,
) -> DownloadSummary:
    """Acquire metadata and PDFs using one polite, sequential HTTP connection."""

    limiter = RequestRateLimiter(request_delay_seconds)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/atom+xml, application/pdf"}
    with httpx.Client(headers=headers, follow_redirects=True, timeout=timeout_seconds) as client:
        papers = fetch_papers(client, limiter, query=query, max_results=max_results)
        return download_papers(client, limiter, papers, raw_dir, overwrite=overwrite)


def _positive_int(value: str) -> int:
    """Argparse converter that rejects zero and negative values."""

    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def build_argument_parser() -> argparse.ArgumentParser:
    """Construct the downloader command-line contract."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default=DEFAULT_QUERY, help="arXiv search_query expression")
    parser.add_argument("--max-results", type=_positive_int, default=DEFAULT_MAX_RESULTS)
    parser.add_argument("--raw-dir", type=Path, default=None)
    parser.add_argument(
        "--request-delay",
        type=float,
        default=MIN_REQUEST_INTERVAL_SECONDS,
        help="Seconds between requests; values below 3 violate arXiv's published limit",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the resumable corpus download command."""

    arguments = build_argument_parser().parse_args(argv)
    if arguments.request_delay < MIN_REQUEST_INTERVAL_SECONDS:
        raise SystemExit("--request-delay must be at least 3 seconds for arXiv")

    settings = get_settings()
    raw_dir = arguments.raw_dir or settings.raw_data_dir
    summary = download_corpus(
        raw_dir,
        query=arguments.query,
        max_results=arguments.max_results,
        request_delay_seconds=arguments.request_delay,
        timeout_seconds=arguments.timeout,
        overwrite=arguments.overwrite,
    )
    print(
        "Download summary: "
        f"requested={summary.requested}, downloaded={summary.downloaded}, "
        f"skipped={summary.skipped}, failed={summary.failed}, "
        f"bytes={summary.downloaded_bytes:,}, manifest={summary.manifest_path}"
    )
    return 1 if summary.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
