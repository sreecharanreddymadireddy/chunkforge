# API contracts and execution flows

This describes the implemented API, not a proposed upload/job system. Interactive
contract: `http://localhost:8000/docs`; machine-readable contract: `/openapi.json`.
OpenAPI is an API specification, not the OpenAI model service.

## What calls what?

```mermaid
sequenceDiagram
    participant U as Angular / curl
    participant A as FastAPI
    participant R as Redis embedding cache
    participant M as BGE model
    participant P as PostgreSQL / pgvector
    participant B as In-memory BM25
    participant X as RRF + cross-encoder
    participant O as Ollama
    U->>A: POST /search or /ask {query,strategy}
    A->>A: Validate DTO; acquire one inference slot
    A->>P: Check indexing manifest; refresh lexical snapshot if changed
    A->>B: Keyword candidates (up to 50)
    A->>R: GET model+representation+text hash
    alt Cache miss / Redis unavailable
        A->>M: Encode query; normalize 384 coordinates
        A->>R: SETEX vector JSON + TTL (best effort)
    end
    A->>P: Cosine candidates (up to 50; same corpus)
    A->>X: RRF merge → top20 → cross-encode → top5
    alt /search
        A-->>U: Ranked evidence + independent scores + mode
    else /ask
        A->>A: Pack numbered evidence, context/output budgets
        A->>O: POST /api/chat; explicit evidence-only prompt
        O-->>A: Completed JSON answer + citation IDs
        A->>A: Validate structure; format citations or degrade
        A-->>U: Answer / abstention / evidence-only
    end
```

The implementation runs BM25 before the dense provider; these aren't concurrent
network branches. Redis is checked before **query inference**, not before every
DB call: manifest/lexical snapshot DB reads can happen first. A cached vector
doesn't bypass DB search, RRF, reranking or generation. Kafka is absent deliberately.

## Endpoint catalog

| Endpoint            | Purpose                                       | Heavy models?                    | Changes state?                                      |
| ------------------- | --------------------------------------------- | -------------------------------- | --------------------------------------------------- |
| GET `/health`       | Process liveness, name/version                | No                               | No                                                  |
| GET `/docs`         | Swagger UI                                    | No                               | No                                                  |
| GET `/openapi.json` | Actual request/response schema                | No                               | No                                                  |
| POST `/search`      | Ranked retrieved evidence                     | BGE + cross-encoder, lazy loaded | May populate embedding cache                        |
| POST `/ask`         | Retrieve once, then grounded local generation | Retrieval models + Ollama        | May populate embedding cache; no answer persistence |

No `/upload`, `/ingest`, `/jobs/{id}`, `/compare`, auth endpoint or readiness route
exists. Compare is two sequential `/search` calls from Angular. Ingestion is CLI
batch or a Kafka file watcher/worker. `/health` doesn't connect to DB/model services.

## Shared input DTO

```json
{
  "query": "How does DivOPD select turns for training?",
  "strategy": "recursive"
}
```

Query is trimmed, nonblank and at most 2,000 characters. Strategy defaults to
recursive; allowed values: fixed, recursive, semantic, sentence_window, late.
FastAPI/Pydantic validate actual input, like Spring MVC + Bean Validation. Type
hints alone aren't runtime validation. Unknown JSON fields currently aren't
explicitly forbidden by this request model; strict extra-field rejection is a gap.

## `/search`: response anatomy

Fields: `query`, `strategy`, `mode`, `reasons`, `milliseconds`, `hits`.
Each hit has `chunk` plus `dense_score`, `bm25_score`, `rrf_score`, `rerank_score`.
The chunk contains source ID/index/text/character offsets, a score and optional
window context. Use `/openapi.json` for exact nullable fields rather than assuming
the frontend adds a confidence field. A stage score can be null if the passage
wasn't returned by that stage. Cosine, BM25, RRF and logits are different scales.

The real DivOPD evidence record in PostgreSQL is recursive chunk 3, source range
`[6901,8682)`, 359 tiktoken units, representation `bounded_piece_mean`, BGE model
revision `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`. Generation later supplies only a
budgeted prefix `[6901,7597)`. Stored chunk end and supplied citation end differ
intentionally; citation metadata describes exactly what the generator saw.

Modes: `hybrid` = both retrieval paths and reranking; `hybrid_unreranked` = fused
results retained after reranker failure; `bm25_only` = dense dependency unavailable
or circuit open. An HTTP200 with degraded mode is not full semantic capability.

## `/ask`: three outcomes

Actual successful response excerpt:

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

`citations` contains cited evidence records; `evidence` contains all packed records.
Each includes local citation ID, document ID, chunk index, source start/end, exact
supplied text and whether truncated. Citation IDs are local to this response,
not database primary keys. The model supplied ID1 but omitted inline `[1]`; the
application appended a validated marker, not new factual content.

| Status                | Answer                                                    | Meaning                                                |
| --------------------- | --------------------------------------------------------- | ------------------------------------------------------ |
| answered              | String with supported-ID citations                        | Structural checks passed; entailment isn't guaranteed  |
| insufficient_evidence | Canonical “I don't know based on the provided documents.” | Empty evidence or model-declared inability             |
| evidence_only         | null; evidence remains                                    | Ollama unavailable/timeout or invalid/truncated output |

Inspect `reasons` and don't show null as a successful generated answer. Temperature
zero and citations aren't proof of truth. The current answer is incomplete relative
to the source; comprehensive answer-quality evaluation is still absent.

## Errors and corner cases

| Response            | Trigger                                                                           | Correct caller behavior                     |
| ------------------- | --------------------------------------------------------------------------------- | ------------------------------------------- |
| 422                 | Blank/oversized query, unknown strategy, invalid request or a pipeline ValueError | Correct input/config; don't retry blindly   |
| 429 + Retry-After2  | Another model-heavy request owns the slot                                         | Back off; avoid parallel local compare      |
| 503                 | Unhandled operational dependency failure                                          | Inspect server logs/dependency reachability |
| 200 + degraded mode | Protected dependency failed; fallback succeeded                                   | Display capability loss                     |
| 200 + evidence_only | Retrieval succeeded, generation failed validation/transport                       | Display evidence, not a fabricated answer   |

FastAPI routes are synchronous and run in its thread pool; a semaphore gates heavy
work to one per backend process. Two Uvicorn workers would each load their own
models and gate—there is no global distributed capacity limit. CORS allows the
configured browser origin, not user authentication. Browser disconnects don't
guarantee cancellation of an already executing synchronous model call.

## Call and inspect independently

```sh
curl -s http://localhost:8000/health
curl -s http://localhost:8000/openapi.json | jq '.paths | keys'
curl -s -H 'Content-Type: application/json' \
  -d '{"query":"How does DivOPD select turns for training?","strategy":"recursive"}' \
  http://localhost:8000/search | jq '{mode,reasons,hits}'
curl --max-time 300 -s -H 'Content-Type: application/json' \
  -d '{"query":"How does DivOPD select turns for training?","strategy":"recursive"}' \
  http://localhost:8000/ask | jq '{status,answer,reasons,citations}'
```

For intermediate top 50/fused candidates use the retrieval CLI `--verbose --output`;
the HTTP contract intentionally returns final evidence, not every internal candidate.
For prompts/raw model JSON use the generation CLI `--output` in an ignored location.
