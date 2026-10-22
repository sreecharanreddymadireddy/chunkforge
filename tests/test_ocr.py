"""OCR routing, real scan recovery, cache invalidation and workload-limit acceptance checks."""

# mypy: disable-error-code=no-untyped-call
import json
import os
from pathlib import Path

import pymupdf
import pytest

from chunkforge.ingestion import parser
from chunkforge.ingestion.ocr import OcrOptions, OcrUnavailable, needs_ocr
from chunkforge.ingestion.ocr_demo import CASES, create_fixtures


@pytest.fixture
def sources(tmp_path: Path) -> dict[str, Path]:
    """Generate five tiny, deterministic PDFs with no private or third-party content."""
    return {p.stem: p for p in create_fixtures(tmp_path / "raw")}


@pytest.fixture
def tessdata() -> Path:
    """Run actual Tesseract where data is installed; do not silently mock recognition."""
    explicit = os.environ.get("CHUNKFORGE_TEST_TESSDATA")
    if explicit:
        path = Path(explicit)
        assert (path / "eng.traineddata").is_file(), "Configured OCR test language data missing"
        return path
    try:
        path = Path(pymupdf.get_tessdata())
    except (OSError, RuntimeError):
        pytest.skip("Install Tesseract English data or set CHUNKFORGE_TEST_TESSDATA")
    if not (path / "eng.traineddata").is_file():
        pytest.skip("English OCR data not installed")
    return path


def test_native_pages_and_blanks_do_not_call_ocr(
    sources: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Digital pages remain unchanged and blank pages must not require an OCR engine."""

    def forbidden(*args: object) -> None:
        raise AssertionError("Unexpected OCR call for native text")

    monkeypatch.setattr(parser, "recognize_page", forbidden)
    parsed = parser.parse_pdf(sources["01_digital"], ocr=OcrOptions(mode="auto"))
    assert parsed.text == parser.parse_pdf(sources["01_digital"]).text
    with pymupdf.open(sources["05_scan_blank_scan"]) as doc:
        assert not needs_ocr(doc[1], "", OcrOptions(mode="auto"))


def test_image_placeholders_are_not_searchable_native_text(sources: dict[str, Path]) -> None:
    """A raster image description must not be mistaken for successfully parsed scan text."""
    parsed = parser.parse_pdf(sources["02_scanned"])
    assert not parsed.text
    assert parsed.statistics.empty_page_count == 1
    assert parsed.statistics.ocr_page_count == 0


def test_auto_routes_large_low_text_scan_but_not_small_logo(sources: dict[str, Path]) -> None:
    """A native caption must not hide the scanned body; small decorations alone do not qualify."""
    with pymupdf.open(sources["04_sparse_native"]) as doc:
        assert needs_ocr(doc[0], "SCAN", OcrOptions(mode="auto"))
        assert not needs_ocr(doc[0], "SCAN", OcrOptions())
        assert not needs_ocr(doc[0], "long native text " * 10, OcrOptions(mode="auto"))
    with pymupdf.open() as doc:
        page = doc.new_page(width=600, height=800)
        image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), False)
        image.clear_with(255)
        page.insert_image(pymupdf.Rect(20, 20, 60, 60), pixmap=image)
        assert not needs_ocr(page, "", OcrOptions(mode="auto"))


def test_missing_language_data_fails_explicitly(sources: dict[str, Path], tmp_path: Path) -> None:
    """Recognition setup failures are operational errors, not fabricated successful text."""
    with pytest.raises(OcrUnavailable, match="language pack"):
        parser.parse_pdf(sources["02_scanned"], ocr=OcrOptions(mode="auto", tessdata=tmp_path))


def test_ocr_pixel_budget_precedes_engine_allocation(sources: dict[str, Path]) -> None:
    """Oversized rasterization is rejected without needing OCR language data."""
    with pytest.raises(RuntimeError, match="pixel budget"):
        parser.parse_pdf(sources["02_scanned"], ocr=OcrOptions(mode="auto", max_pixels=1))


@pytest.mark.parametrize("name", list(CASES))
def test_real_ocr_recovers_each_fixture_and_preserves_spans(
    sources: dict[str, Path], tessdata: Path, name: str
) -> None:
    """Use real recognition on scans, and verify source character spans including blank pages."""
    parsed = parser.parse_pdf(sources[name], ocr=OcrOptions(mode="auto", tessdata=tessdata))
    count = 0
    for span, (method, expected) in zip(parsed.page_spans, CASES[name], strict=True):
        actual = parsed.text[span.char_start : span.char_end]
        is_ocr = method in ("scan", "sparse")
        assert span.extraction_method == ("ocr" if is_ocr else "native")
        count += is_ocr
        if expected:
            assert " ".join(expected.casefold().split()) in " ".join(actual.casefold().split())
        else:
            assert actual == ""
    assert parsed.statistics.ocr_page_count == count
    assert parsed.page_spans[-1].char_end == len(parsed.text)


def test_real_ocr_page_limit_and_force_mode(sources: dict[str, Path], tessdata: Path) -> None:
    """Bound the number of recognized pages; force is an explicit operator choice."""
    with pytest.raises(RuntimeError, match="page budget"):
        parser.parse_pdf(
            sources["05_scan_blank_scan"],
            ocr=OcrOptions(mode="auto", tessdata=tessdata, max_pages=1),
        )
    parsed = parser.parse_pdf(
        sources["01_digital"], ocr=OcrOptions(mode="force", tessdata=tessdata)
    )
    assert parsed.statistics.ocr_page_count == 1
    assert "Native extraction" in parsed.text


def test_processed_cache_checks_policy_source_and_output(
    sources: dict[str, Path], tmp_path: Path
) -> None:
    """Settings changes trigger parsing, while source/output hashes reject stale sidecars."""
    raw = tmp_path / "native"
    raw.mkdir()
    path = raw / "native.pdf"
    path.write_bytes(sources["01_digital"].read_bytes())
    output = tmp_path / "processed"
    assert parser.parse_corpus(raw, output).parsed_documents == 1
    assert parser.parse_corpus(raw, output).skipped_documents == 1
    assert parser.parse_corpus(raw, output, ocr=OcrOptions(mode="auto")).parsed_documents == 1
    (output / "native.txt").write_text("corrupt cached text")
    assert parser.parse_corpus(raw, output, ocr=OcrOptions(mode="auto")).parsed_documents == 1
    metadata_path = output / "native.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["source_sha256"] = "invalid"
    metadata_path.write_text(json.dumps(metadata))
    assert parser.parse_corpus(raw, output, ocr=OcrOptions(mode="auto")).parsed_documents == 1
    assert "native_characters" in json.loads(metadata_path.read_text())["page_spans"][0]


def test_ocr_options_reject_invalid_language_and_resource_limits() -> None:
    """Reject path-shaped language names and out-of-range allocation options."""
    for values in ({"language": "../../secret"}, {"dpi": 0}, {"max_pages": 0}):
        with pytest.raises(ValueError):
            OcrOptions.model_validate(values)


def test_demo_rejects_unrelated_input_and_preserves_existing_files(tmp_path: Path) -> None:
    """A fixture command cannot overwrite an arbitrary PDF or sweep unrelated inputs."""
    raw = tmp_path / "raw"
    raw.mkdir()
    existing = raw / "01_digital.pdf"
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(existing)
    original = existing.read_bytes()
    with pytest.raises(ValueError, match="not a synthetic"):
        create_fixtures(raw)
    assert existing.read_bytes() == original
    unrelated = raw / "unrelated.pdf"
    unrelated.write_bytes(original)
    with pytest.raises(ValueError, match="unrelated PDFs"):
        create_fixtures(raw)
    assert unrelated.read_bytes() == original


def test_language_pack_hash_changes_cache_identity(tmp_path: Path) -> None:
    """A replaced language model invalidates parsing without leaking its local directory."""
    data = tmp_path / "eng.traineddata"
    data.write_bytes(b"first fixture language identity")
    options = OcrOptions(mode="auto", tessdata=tmp_path)
    first = options.signature()
    data.write_bytes(b"second fixture language identity")
    assert first != options.signature()
    assert "tessdata" not in first
    assert str(tmp_path) not in json.dumps(first)
