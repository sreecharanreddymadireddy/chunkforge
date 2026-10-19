# Chunking comparison

Measured on full cleaned source documents; every source span and token budget validated.
These are chunk statistics, not retrieval-quality measurements or a strategy ranking.

Model: `BAAI/bge-small-en-v1.5`; device: `cpu`; model input cap: 512; encoding: `cl100k_base`.
Core budget: 512; fixed overlap: 50; semantic threshold: 0.5; sentence window: ±1; late core: 128.

Late uses mean pooling inside separate BGE-sized passages, not whole-paper context. Its smaller core budget makes counts unsuitable for a same-size quality comparison.
Sentence-window counts describe indexed anchors; surrounding context can be larger.
Times include tokenization/inference and vary by hardware; model loading is excluded.

| Document | Strategy | Chunks | Avg core tokens | Max core tokens | Seconds |
|---|---|---:|---:|---:|---:|
| 2609.34838v1 | fixed | 54 | 512.0 | 512 | 0.524 |
| 2609.34838v1 | recursive | 55 | 454.56 | 512 | 0.07 |
| 2609.34838v1 | semantic | 163 | 154.25 | 511 | 6.916 |
| 2609.34838v1 | sentence_window | 895 | 28.82 | 510 | 2.02 |
| 2609.34838v1 | late | 221 | 113.26 | 128 | 4.719 |
| 2609.34842v1 | fixed | 64 | 507.77 | 512 | 0.673 |
| 2609.34842v1 | recursive | 66 | 444.68 | 512 | 0.087 |
| 2609.34842v1 | semantic | 189 | 155.81 | 512 | 7.871 |
| 2609.34842v1 | sentence_window | 728 | 40.99 | 512 | 1.962 |
| 2609.34842v1 | late | 258 | 113.88 | 128 | 5.667 |
| 2609.34847v1 | fixed | 35 | 508.94 | 512 | 0.225 |
| 2609.34847v1 | recursive | 34 | 473.91 | 512 | 0.057 |
| 2609.34847v1 | semantic | 71 | 227.51 | 508 | 4.976 |
| 2609.34847v1 | sentence_window | 528 | 31.19 | 474 | 1.699 |
| 2609.34847v1 | late | 143 | 112.86 | 128 | 3.208 |
| 2609.34849v1 | fixed | 91 | 507.75 | 512 | 1.841 |
| 2609.34849v1 | recursive | 89 | 468.62 | 512 | 0.152 |
| 2609.34849v1 | semantic | 158 | 264.61 | 512 | 12.705 |
| 2609.34849v1 | sentence_window | 1033 | 41.16 | 509 | 4.011 |
| 2609.34849v1 | late | 369 | 113.14 | 128 | 8.593 |
| 2609.34850v1 | fixed | 44 | 504.36 | 512 | 0.341 |
| 2609.34850v1 | recursive | 43 | 466.09 | 512 | 0.051 |
| 2609.34850v1 | semantic | 113 | 177.94 | 512 | 6.289 |
| 2609.34850v1 | sentence_window | 711 | 28.96 | 512 | 1.314 |
| 2609.34850v1 | late | 174 | 115.36 | 128 | 3.816 |

## Interpretation

Fixed windows are a size baseline; overlap repeats text. Recursive packing trades density for structural boundaries. Semantic counts depend on similarity thresholds and sentence segmentation. Sentence anchors store their context separately. Late vectors retain only bounded passage context. Retrieval evaluation must determine whether any of these trade-offs improve answers.

Reproduce with `make chunk-compare`; source hashes are in the adjacent JSON report.
