"""Opt-in Tesseract page recognition with explicit detection and resource limits."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import pymupdf
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from chunkforge.config import Settings


class OcrUnavailable(RuntimeError):
    """An engine/language setup failure must not be acknowledged as a poison PDF."""


class OcrOptions(BaseModel):
    """Keep OCR off by default; auto selects low-text pages with a large raster image."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    mode: Literal["off", "auto", "force"] = "off"
    language: str = Field(default="eng", pattern=r"^[a-zA-Z0-9_]+(?:\+[a-zA-Z0-9_]+)*$")
    dpi: int = Field(default=200, ge=72, le=400)
    min_native_characters: int = Field(default=32, ge=1)
    min_image_coverage: float = Field(default=0.6, gt=0, le=1)
    max_pages: int = Field(default=50, ge=1)
    max_pixels: int = Field(default=20_000_000, ge=1)
    tessdata: Path | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> OcrOptions:
        """Use the same extraction configuration for batch and Kafka workers."""
        return cls(
            mode=settings.ocr_mode,
            language=settings.ocr_language,
            dpi=settings.ocr_dpi,
            min_native_characters=settings.ocr_min_native_characters,
            min_image_coverage=settings.ocr_min_image_coverage,
            max_pages=settings.ocr_max_pages,
            max_pixels=settings.ocr_max_pixels,
            tessdata=settings.ocr_tessdata,
        )

    def signature(self) -> dict[str, object]:
        """Version processed output by policy, without exposing machine-specific data paths."""
        signature = cast(dict[str, object], self.model_dump(mode="json", exclude={"tessdata"}))
        signature["pymupdf_version"] = pymupdf.VersionBind
        packs: dict[str, str] = {}
        if self.mode != "off":
            try:
                folder = Path(
                    pymupdf.get_tessdata(  # type: ignore[no-untyped-call]
                        str(self.tessdata) if self.tessdata else None
                    )
                )
                for language in self.language.split("+"):
                    path = folder / f"{language}.traineddata"
                    if path.is_file():
                        packs[language] = hashlib.sha256(path.read_bytes()).hexdigest()
            except (RuntimeError, OSError):
                pass  # Native-text pages still work without an OCR installation.
        signature["language_data_sha256"] = packs
        return signature


def needs_ocr(page: pymupdf.Page, native_text: str, options: OcrOptions) -> bool:
    """Select full-page scans conservatively; do not OCR blank pages or small logos."""
    if options.mode == "off":
        return False
    if options.mode == "force":
        return True
    if sum(c.isalnum() for c in native_text) >= options.min_native_characters:
        return False
    area = float(page.rect.width * page.rect.height)
    if area <= 0:
        return False
    images = page.get_image_info()
    # Largest visible image, not a sum that double-counts overlapping logos/tiles.
    return any(
        float(
            (pymupdf.Rect(image["bbox"]) & page.rect).get_area()  # type: ignore[no-untyped-call]
        )
        / area
        >= options.min_image_coverage
        for image in images
    )


def recognize_page(page: pymupdf.Page, options: OcrOptions) -> pymupdf.TextPage:
    """Recognize one bounded rasterization; callers reuse this TextPage for extraction."""
    pixels = math.ceil(page.rect.width * options.dpi / 72) * math.ceil(
        page.rect.height * options.dpi / 72
    )
    if pixels > options.max_pixels:
        raise RuntimeError("OCR page exceeds configured raster pixel budget")
    try:
        data_dir = Path(
            pymupdf.get_tessdata(  # type: ignore[no-untyped-call]
                str(options.tessdata) if options.tessdata else None
            )
        )
    except (RuntimeError, OSError) as exc:
        raise OcrUnavailable(
            "OCR language data unavailable; install Tesseract or set CHUNKFORGE_OCR_TESSDATA"
        ) from exc
    if any(
        not (data_dir / f"{lang}.traineddata").is_file() for lang in options.language.split("+")
    ):
        raise OcrUnavailable("Requested OCR language pack is missing from the tessdata directory")
    try:
        return cast(
            pymupdf.TextPage,
            page.get_textpage_ocr(  # type: ignore[no-untyped-call]
                language=options.language, dpi=options.dpi, full=True, tessdata=str(data_dir)
            ),
        )
    except (RuntimeError, OSError) as exc:
        raise OcrUnavailable(
            "Tesseract page recognition failed; verify engine and language data"
        ) from exc
