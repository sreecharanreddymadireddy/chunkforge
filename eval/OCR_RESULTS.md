# OCR acceptance and isolated vector-search verification

Verified on 2026-10-20 with real PyMuPDF/Tesseract recognition and English
`tessdata_fast` language data, followed by real BGE/pgvector indexing. These are
controlled synthetic fixtures, not a representative OCR accuracy benchmark.

| Fixture | Pages | OCR pages | Verified outcome |
|---|---:|---:|---|
| Digital text | 1 | 0 | Native output unchanged |
| Image-only scan | 1 | 1 | Expected delivery-instructions text recovered |
| Digital + scanned pages | 2 | 1 | Native page preserved; scanned Kafka instructions recovered |
| Native caption + scanned body | 1 | 1 | Body recovered despite existing caption |
| Scan + blank + scan | 3 | 2 | Both scanned texts recovered; blank page empty; character spans valid |

All five phrases matched after whitespace normalization. No character-error-rate,
handwriting, table reconstruction or broad noisy/rotated-scan quality score is
claimed. All eight rendered pages were visually inspected. Recognition used
200 DPI; input scans were generated at 150 DPI from controlled text.

## Persistence and retrieval

Only the dedicated `chunkforge_ocr_demo` and `chunkforge_ocr_demo_runs` tables
received these fixtures. Main corpus counts matched before and after indexing.

| Strategy | Completed documents | Stored chunks |
|---|---:|---:|
| fixed | 5 | 5 |
| recursive | 5 | 5 |
| semantic | 5 | 6 |
| sentence_window | 5 | 7 |
| late | 5 | 5 |

Repeated batch parsing skipped all five unchanged processed outputs. Repeated
indexing returned unchanged outcomes across every selected strategy without
changing stored counts.

Actual dense query: **When should Kafka consumers commit offsets?**

Recursive rank one was `03_mixed`, whose recognized source states that consumers
commit offsets after database writes succeed. Recorded cosine similarity was
approximately 0.8001. This is a five-document dense-search smoke test, not a
hybrid-retrieval benchmark or calibrated confidence.

## Automated checks

Final full suite: **90 passed, 1 skipped**. Real OCR and isolated PostgreSQL
integration were enabled; Redis integration was not enabled in this run. Lint,
strict type checking and whitespace checks passed.

Added checks cover native bypass, dominant-image routing, image-placeholder
rejection, five real OCR cases, blank-page offsets, force mode, language setup
errors, page/pixel limits, cache identity, source/output corruption, fixture
directory safety and Kafka non-commit on an OCR setup failure.

The Dockerfile and CI configuration now supply English OCR data; a new full Docker
image build and a remote CI run were not performed for this local verification.
See [setup, commands and limits](../docs/OCR.md) for reproduction.
