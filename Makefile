SHELL := /bin/sh
UV_VERSION := 0.8.22
UV ?= .venv-bootstrap/bin/uv
DEV_EXTRAS := --extra dev --extra api --extra ingestion --extra chunking --extra storage --extra resilience --extra retrieval --extra generation --extra kafka
export UV_CACHE_DIR := $(CURDIR)/.uv-cache

.PHONY: help bootstrap-uv setup lock lint format typecheck test check \
	ingest embed eval api ui docker-up docker-down docker-logs docker-ps

help:
	@echo "ChunkForge commands"
	@echo "  make setup        Bootstrap uv and install current development dependencies"
	@echo "  make check        Run lint, type checking, and tests"
	@echo "  make docker-up    Start PostgreSQL, Redis, Kafka, and Ollama"
	@echo "  make docker-down  Stop the local dependency stack"
	@echo "  make ingest       Run batch ingestion (implemented in Phase 1)"
	@echo "  make chunk-compare Compare five chunking strategies on five complete papers"
	@echo "  make embed        Index corpus chunks in pgvector; ARGS='--max-documents 1' for a pilot"
	@echo "  make search       Hybrid search; ARGS='--query \"your question\" --strategy recursive'"
	@echo "  make eval         Run retrieval evaluation (implemented in Phase 7)"
	@echo "  make api          Run FastAPI with reload"
	@echo "  make ui           Run the Angular development server (implemented in Phase 9)"

bootstrap-uv:
	@test -x "$(UV)" || (python3 -m venv .venv-bootstrap && \
		.venv-bootstrap/bin/python -m pip install --disable-pip-version-check "uv==$(UV_VERSION)")

setup: bootstrap-uv
	$(UV) sync $(DEV_EXTRAS)

lock: bootstrap-uv
	$(UV) lock

lint:
	.venv/bin/ruff check .

format:
	.venv/bin/ruff format .

typecheck:
	.venv/bin/mypy chunkforge tests

test:
	.venv/bin/python -m pytest

check: lint typecheck test

.PHONY: chunk-compare
chunk-compare:
	$(UV) run $(DEV_EXTRAS) --extra ml python -m chunkforge.chunking.compare

ingest:
	$(UV) run $(DEV_EXTRAS) python -m chunkforge.ingestion.downloader
	$(UV) run $(DEV_EXTRAS) python -m chunkforge.ingestion.parser

embed:
	$(UV) run $(DEV_EXTRAS) --extra ml python -m chunkforge.embeddings.index_corpus $(ARGS)

.PHONY: search
search:
	$(UV) run $(DEV_EXTRAS) --extra ml python -m chunkforge.retrieval.demo $(ARGS)

.PHONY: ask
ask:
	$(UV) run $(DEV_EXTRAS) --extra ml python -m chunkforge.generation.demo $(ARGS)

.PHONY: kafka-watch kafka-consume
kafka-watch:
	$(UV) run --inexact $(DEV_EXTRAS) --extra ml python -m chunkforge.ingestion.kafka_pipeline watch $(ARGS)

kafka-consume:
	$(UV) run --inexact $(DEV_EXTRAS) --extra ml python -m chunkforge.ingestion.kafka_pipeline consume $(ARGS)

eval:
	$(UV) run --inexact $(DEV_EXTRAS) --extra ml python -m chunkforge.eval.run_eval $(ARGS)

api:
	$(UV) run --inexact $(DEV_EXTRAS) --extra ml uvicorn chunkforge.api.main:app --reload --host 127.0.0.1 --port 8000

ui:
	cd frontend && npm start

docker-up:
	docker compose up -d --wait

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

docker-ps:
	docker compose ps
