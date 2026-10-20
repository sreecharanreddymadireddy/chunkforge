# Local application verification: Phases 6–10

These are local smoke tests and an exploratory retrieval pilot, not production
availability, latency or hallucination guarantees. Runtime: macOS ARM64, 8 GB RAM,
Python 3.12, CPU inference; dependency services run in Docker Desktop.

## Corpus

The 75-document vector target completed across all five strategies:

| Strategy | Documents | Stored chunks |
|---|---:|---:|
| fixed | 75 | 3,881 |
| recursive | 75 | 3,897 |
| semantic | 75 | 10,523 |
| sentence_window | 75 | 56,276 |
| late | 75 | 15,523 |

Downloaded raw corpus remains 200 PDFs; only 75 are indexed for v1.

## Generation and API

A real POST `/ask` with question **How does DivOPD select turns for training?**
and strategy `recursive` completed with:

```json
{
  "status": "answered",
  "answer": "DivOPD selects turns based on coverage and focus. [1]",
  "retrieval_mode": "hybrid",
  "reasons": [],
  "model": "llama3.2:1b",
  "prompt_eval_count": 335,
  "eval_count": 28,
  "citation_format": "application_suffix"
}
```

Citation 1 maps to document `2609.34838v1`, recursive chunk 3, source characters
`[6901,7597)`. Evidence is marked truncated. The model supplied `citation_ids:[1]`
without an inline marker; the application appended `[1]` after validating the ID.
This is formatting, not a factual entailment check. The concise answer omits some
source details; successful structure is not comprehensive answer quality.

An isolated real generation check using that previously retrieved source refused
**What is the private access code of a person named Zephyr?** with the canonical
`I don't know based on the provided documents.` and no citations. This verifies
one unsupported-question case, not a calibrated refusal policy.

The isolated supported-generation call took approximately 154 seconds, including
model loading. Do not interpret it as end-to-end API latency. The small local
model and CPU configuration are resource-driven compromises. Invalid/truncated
output and timeouts return explicit evidence-only results.

## Evaluation

[RESULTS.md](RESULTS.md) and [RESULTS.json](RESULTS.json) record ten seed questions
against the same frozen set of 60 documents for all strategies. Labels concentrate
on three papers; thirty independently authored questions remain required before
the final benchmark. No user homework was fabricated.

Why recursive led this pilot:

1. Its mean evidence recall@5 was 0.95 versus 0.80 for semantic and 0.70 for fixed.
2. For seed-03 (coverage versus focus), recursive chunk 3 recovered the labeled
   passage at rank 2; fixed returned chunks 32, 19, 18, 48 and 45 without that span.
3. Recursive recovered one of two seed-10 passages at rank 2; other strategies
   recovered neither under this exact coverage rule. It still missed the other hop.
4. Those traces explain concrete score differences, not a universal causal advantage:
   budgets, boundaries, pooling and ranking interact.
5. Source-concentrated labels and an 80% character-coverage judgment limit inference;
   broader independent labels are required before choosing defaults.

## Angular

Real headless Chrome against live FastAPI: the default DivOPD search returned
scored source cards, live comparison returned two strategy panels, and the 390 px mobile layout had
no horizontal overflow. No browser JavaScript errors were observed. Frontend suite:
three tests. Production build passed: approximately 278 KB initial raw / 76 KB
estimated transfer. This isn't a complete accessibility/compatibility audit.

## Kafka

A small PDF was rendered from actual QiYao-M processed text in a temporary raw
directory. Isolated topics/tables kept the fixture out of the production corpus.
The producer published while the consumer was absent; after startup, the consumer
retrieved offset 0 and completed all five strategies.

| Strategy | First ingest | Replay |
|---|---:|---|
| fixed | 1 chunk | unchanged |
| recursive | 1 chunk | unchanged |
| semantic | 1 chunk | unchanged |
| sentence_window | 11 chunks | unchanged |
| late | 5 chunks | unchanged |

Before first commit, committed offset was unset (`-1001`, an SDK sentinel).
Partition high watermark was 1; after processing, committed offset was 1 and lag
was 0. Republished input arrived at offset 1. Stored counts remained identical.
Temporary smoke topics/tables were removed by successful test cleanup.

Kafka's log path was corrected to `/var/lib/kafka/data`, matching its named volume.
Existing metadata/topic files were backed up from the stopped broker and copied
into that volume before recreation. Existing topics survived. Single-broker
durability is not high availability; volume loss still requires independent recovery.

## Test coverage and remaining gates

Full Python suite including live PostgreSQL/Redis integration: **75 passed**, 88%
coverage. Ruff and strict mypy are separate checks. Frontend build/tests have their
own GitHub Actions job. Model downloads aren't required by unit tests.

Not claimed: final 40-question evaluation, production authentication, dependency
readiness, multi-broker HA, distributed PDF storage, load testing, or successful
all-container deployment on this memory-limited machine. Verified local mode:
host API/UI with container dependencies.
