# Where it runs, how to run it, and how to debug it

## Deployment inventory — verified local, no invented cloud endpoint

| Component               | Actual runtime                   | Address / persistence                                      |
| ----------------------- | -------------------------------- | ---------------------------------------------------------- |
| Source                  | Local Git checkout + GitHub main | `sreecharanreddymadireddy/chunkforge`                      |
| Angular                 | Host Node dev server             | http://localhost:4200                                      |
| FastAPI                 | Host `.venv` / Uvicorn           | http://localhost:8000; docs at `/docs`                     |
| PostgreSQL16 + pgvector | Docker Desktop                   | Host5433 → container 5432; postgres_data volume            |
| Redis                   | Docker Desktop                   | Host6379; redis_data volume; reconstructable embeddings    |
| Kafka                   | Docker Desktop                   | Host9092; internal 29092; kafka_data log volume            |
| Ollama / llama 3.2:1b   | Docker Desktop, CPU inference    | Host11434; ollama_data weights                             |
| AWS                     | **Not deployed**                 | No public application URL; `aws/DEPLOY.md` is operator-led |
| GitHub Actions          | Remote CI                        | Tests/build, not application hosting                       |

These are local URLs: sharing localhost doesn't expose your app to another person's
computer. Sharing GitHub shares source, not a live application. The dependency
Compose ports currently bind all interfaces by default; use a trusted network and
firewall, or explicitly bind them to loopback before wider exposure. No auth is
implemented; don't port-forward this demo publicly.

Repo path on this machine: `/Users/sreecharanreddymadireddy/Documents/GENAI/chunkforge`.
Relative commands below run there unless a block explicitly changes directories.
The optional `app` Compose profile has API/UI images, but complete all-container
deployment has not been verified under this laptop's memory limit.

## First clone / rebuild

```sh
cp .env.example .env
# Edit .env: if local5432 is occupied, set POSTGRES_PORT=5433 and use localhost:5433 in the DSN.
make setup
.venv-bootstrap/bin/uv sync --extra dev --extra api --extra ingestion --extra chunking \
  --extra storage --extra resilience --extra retrieval --extra generation --extra kafka --extra ml
make docker-up
docker compose exec ollama ollama pull llama3.2:1b
```

`make setup` installs lightweight development extras; model operations also need
`ml`. `uv` installs Python libraries into `.venv`, analogous to resolving Maven
dependencies, not compiling a JAR. Do not change/synchronize the active environment
while indexing/servers are running. `.venv` may intentionally lack pip; use uv.

```sh
# First corpus acquisition; don't repeat if your manifest/files already exist.
make ingest
make embed ARGS='--max-documents 75'
# First frontend dependency install:
cd frontend
npm ci
```

Model downloads and initial embedding take time/disk. Raw200 and indexed 75 are
different counts. Corpus files aren't in Git, so cloning alone won't reproduce
search data. A second embedding run skips unchanged completed document/strategy
fingerprints; interrupted strategy work is recomputed transactionally.

### Exact example sources versus a new discovery corpus

The default arXiv query is time-dependent: running it later may fetch different
papers. Don't expect the recorded benchmark from a fresh “latest papers” query.
For a **fresh empty checkout only**, acquire the three versioned public sources
used by seed labels. Respect source availability/licensing and the three-second
request interval:

```sh
curl --fail --location --max-time 120 --output data/raw/2609.34838v1.pdf https://arxiv.org/pdf/2609.34838v1
sleep 3
curl --fail --location --max-time 120 --output data/raw/2609.34842v1.pdf https://arxiv.org/pdf/2609.34842v1
sleep 3
curl --fail --location --max-time 120 --output data/raw/2609.34847v1.pdf https://arxiv.org/pdf/2609.34847v1
.venv/bin/python -m chunkforge.ingestion.parser
make embed ARGS='--max-documents 3'
make eval ARGS='--pilot --documents 3 --output .learning/THREE_DOCUMENT_PILOT.md'
```

This is an example-source bootstrap, **not the published 60-document pilot**.
Manual downloads don't create the batch acquisition manifest. Parser/evaluation
source-quote checks still apply; investigate source/version/parser differences
if a quote cannot be found. Published pilot JSON records its exact 60 document
IDs and gold-source hashes. Automatic pinned-corpus reacquisition isn't implemented;
don't overwrite historical reports with a differently scoped run.

## Daily start: two terminals, no repeated ingestion needed

```sh
# Terminal A, repository root
make docker-up
make api
```

```sh
# Terminal B, repository root
make ui
```

Visit localhost:4200, choose recursive and retrieve the DivOPD example. Then choose
Ask with citations. Host API uses localhost service ports; container API uses
Compose DNS names. Don't use postgres:5432 from a host process or localhost:5433
from inside the API container.

## Inspect data with SQL — enter the database client first

```sh
docker compose exec postgres psql -U chunkforge -d chunkforge
```

Only after seeing `chunkforge=#`, run:

```sql
\dt
\d chunkforge_chunks
\di
SELECT strategy,count(*) AS documents,sum(chunk_count) AS chunks
FROM chunkforge_chunks_runs GROUP BY strategy ORDER BY strategy;
SELECT doc_id,strategy,chunk_index,char_start,char_end,token_count,left(text,160)
FROM chunkforge_chunks WHERE doc_id='2609.34838v1' AND strategy='recursive'
ORDER BY chunk_index LIMIT 5;
SELECT pg_size_pretty(pg_total_relation_size('chunkforge_chunks'));
\watch 10
```

`\watch` repeats the last SQL query; Ctrl-C stops watching; `\q` leaves psql.
A Docker Exec `#` prompt is a Linux shell, not a SQL prompt. That's why raw
`SELECT count(*)` previously produced “syntax error”/“FROM not found.” In a VS Code
DB client use host localhost, port 5433, DB/user chunkforge, password from your
local `.env`; never copy credentials into screenshots or tracked connection files.

## Debug one flow at a time

| Flow        | Command / observation                                                                | Best breakpoint                              |
| ----------- | ------------------------------------------------------------------------------------ | -------------------------------------------- |
| Download    | `python -m chunkforge.ingestion.downloader --max-results 5` using `.venv/bin/python` | downloader download/manifest decision        |
| Parse       | `.venv/bin/python -m chunkforge.ingestion.parser`                                    | `parse_pdf`, output text/provenance          |
| Chunk       | `make chunk-compare`; inspect `eval/CHUNKING_RESULTS.json`                           | chosen strategy `.chunk`, `make_chunk`       |
| Embed/store | `make embed ARGS='--max-documents 75'`; observe skip messages                        | `embed_chunks`, `replace_document`           |
| Dense only  | `make embed ARGS='--search-only --query DivOPD --strategies recursive'`              | store `.search`                              |
| Hybrid      | CLI below; inspect dense/lexical/fused/final arrays                                  | `RetrievalPipeline.search`, fusion, reranker |
| Cache       | repeat CLI query; compare cache counters; Redis SCAN/TTL                             | `EmbeddingCache.get_or_compute`              |
| Fallback    | safe injected dense failure CLI below                                                | retry, breaker `.call`, keyword fallback     |
| Generation  | explicit trace file below                                                            | `build_prompt`, `RagService.ask`             |
| API         | curl independent of browser; server terminal logs                                    | `main.search/ask`, `LocalBackend._pipeline`  |
| Angular     | browser Network tab `/api/search`; check body/status/response                        | service `.search`, component `.run`          |
| Kafka       | watcher/consumer terminals; group offsets                                            | `process_message`, `PDFProcessor.__call__`   |

```sh
make search ARGS='--query "How does DivOPD select turns for training?" --strategy recursive --repeat 2 --verbose --output .learning/search-debug.json'
make search ARGS='--query DivOPD --repeat 4 --fail-dense'
make ask ARGS='--query "How does DivOPD select turns for training?" --strategy recursive --output .learning/ask-debug.json'
docker compose exec redis redis-cli --scan --pattern 'chunkforge:embedding:v1:*'
docker compose exec redis redis-cli INFO stats
```

Use `TYPE`, `TTL` and `GET` with one actual key from SCAN; don't use `KEYS *` on
large production caches and never FLUSHALL to debug a miss. A JSON vector is a
Redis string containing 384 numbers; TTL86400 is the configured maximum lifetime,
not a promise every observed entry still has a full day left.

### Python debugger without another plugin

```sh
.venv/bin/python -m pdb -m chunkforge.retrieval.demo --query DivOPD --strategy recursive
```

At `(Pdb)`: `b chunkforge/retrieval/pipeline.py:search` may require a concrete line
number; use `list`/`l` and set `b filename:line` at the function body, `c` continue,
`n` next, `s` step into, `p variable` inspect, `q` quit. In VS Code select `.venv/bin/python`,
set a breakpoint in these same functions and launch module `uvicorn` with arguments
`chunkforge.api.main:app --host 127.0.0.1 --port 8000`, **without --reload** while
debugging so the child reloader doesn't hide your breakpoint. Stop the existing
port 8000 server first. Avoid printing credentials, full prompts or private corpus
text to public logs; local debug artifacts belong under ignored `.learning/`.

## Kafka add-one-document drill

Start `make kafka-watch` and `make kafka-consume` in separate terminals. The first
watcher baselines existing raw files. Add a new PDF, wait for `published`, then
consumer `indexed`; query completed runs and search its content. Watcher/consumer
share file paths; Kafka contains only metadata. To inspect lag:

```sh
docker compose exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 --all-groups --describe
docker compose logs --tail=80 kafka
```

Don't demonstrate replay by deleting a group or resetting offsets on the entire
corpus. The isolated replay verification already records unchanged counts. For
your own experiment use a dedicated topic/group/raw directory and known fixture.

## Failure diagnosis, in the right order

| Symptom                          | Inspect                              | Likely action                                          |
| -------------------------------- | ------------------------------------ | ------------------------------------------------------ |
| Connection refused 8000          | API terminal, occupied port          | Start correct server; don't reinstall Docker           |
| UI502/404                        | dev proxy, server URL                | `/api/search` browser → `/search` backend              |
| DB connection refused            | Compose health/host 5433/DSN         | Correct network endpoint; preserve volume              |
| No compatible chunks             | indexed strategy/model identity      | Index correct representation; don't mix vectors        |
| 429                              | another heavy request                | Wait/retry; stop parallel model jobs                   |
| Slow first query                 | model loading + snapshot creation    | Warm once; don't treat it as steady-state latency      |
| Faster cache, still slow request | reranker/Ollama CPU                  | Cache doesn't eliminate every stage                    |
| BM25-only                        | dense reason and breaker state       | Check model/DB; wait recovery probe                    |
| Ask evidence-only                | generation reasons; Ollama logs/list | Check model, timeout, JSON/citations, memory           |
| Worker stuck before publish      | raw-file read/stability              | Check file hydration/copy completion, not just Kafka   |
| Consumer lag grows               | worker logs/CPU/storage              | Identify processing bottleneck before adding consumers |

Actual known issues and fixes are documented in `GAP_AUDIT.md`; don't invent a
production incident from a local smoke-test failure.

## Stop, back up, restart safely

Ctrl-C API/UI/watchers/workers, then `docker compose down` preserves named volumes.
`down -v` deletes persistent state and is not a routine shutdown. Never prune all
Docker volumes or reset the database to fix a query issue. For a backup use
`pg_dump` and record restore instructions; a volume alone isn't an independent
backup. Kafka migration backup from this build remains at
`/private/tmp/chunkforge-kafka-backup.9QZ56I`; temporary directories can be cleaned
by the OS, so move it to an approved durable backup location if you need retention.

Current AWS status remains **not deployed**. Follow `aws/DEPLOY.md` yourself, then
record the real URL, resources and verification timestamp here. No credential or
cloud bill has been invented to make this inventory appear finished.
