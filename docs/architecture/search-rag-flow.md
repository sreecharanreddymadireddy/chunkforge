# Implemented question lifecycle

```mermaid
flowchart LR
    UI["Angular / curl"] --> DTO["FastAPI validate DTO + one-slot gate"]
    DTO --> SNAP["DB manifest check / lexical snapshot refresh"]
    SNAP --> BM["BM25 top50"]
    BM --> QE["Redis GET query vector"]
    QE -->|"miss / cache outage"| BGE["BGE query inference → best-effort SETEX"]
    QE -->|"hit"| PG["pgvector cosine top50"]
    BGE --> PG
    BM --> RRF["RRF rank merge"]
    PG --> RRF
    RRF --> CROSS["Cross-encode20 → return5"]
    CROSS --> SEARCH["/search evidence + scores"]
    CROSS --> PROMPT["/ask numbered evidence + question"]
    PROMPT --> O["Ollama / llama3.2:1b"] --> VALID["Validate JSON + citation IDs"]
    VALID --> ANSWER["Cited answer / abstention / evidence-only"]
    SEARCH --> UI
    ANSWER --> UI
```

BM25 and dense are complementary rankings over the same compatible source corpus.
Redis contains embeddings, **not answers**. A hit skips inference only. Kafka is
not in this request path. No response cache or job-status store exists.

Dense failure → BM25-only; open breaker → skip dense provider; reranker failure →
fused unreranked evidence; Ollama timeout/invalid reply → evidence-only. Warm
lexical fallback still depends on previously loaded source data. Unsupported
questions can yield canonical abstention. Citations aren't entailment proof.

See [API contracts and exact response examples](../API_FLOWS.md),
[actual generation verification](../../eval/PHASES_6_TO_10.md), and
[full record-by-record journey](../END_TO_END_GUIDE.md).
