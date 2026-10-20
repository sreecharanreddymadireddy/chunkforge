"""Inspect the exact evidence prompt and run grounded local answer generation."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import httpx

from chunkforge.config import get_settings
from chunkforge.generation.rag import RagService, Snapshot, build_prompt
from chunkforge.retrieval.runtime import SearchRuntime
from chunkforge.store.pgvector_store import STRATEGIES


def main() -> None:
    """Print prompt/response artifacts without publishing corpus or private configuration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, action="append")
    parser.add_argument("--strategy", choices=STRATEGIES, default="recursive")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    runtime = SearchRuntime.create(settings)
    try:
        pipeline = runtime.pipeline(args.strategy)
        results: list[dict[str, object]] = []
        exchange: dict[str, object] = {}

        def capture(response: httpx.Response) -> None:
            """Keep inspectable local prompt/model output for the learning walkthrough."""
            response.read()
            exchange["request"] = json.loads(response.request.content)
            exchange["response"] = response.json()

        with httpx.Client(
            base_url=str(settings.ollama_base_url),
            trust_env=False,
            timeout=httpx.Timeout(settings.generation_timeout_seconds, connect=3),
            event_hooks={"response": [capture]},
        ) as c:
            service = RagService(
                pipeline,
                c,
                settings.ollama_model,
                settings.generation_context_size,
                settings.generation_output_tokens,
                settings.generation_template_reserve,
                settings.generation_source_bytes,
                settings.generation_temperature,
                settings.generation_threads,
            )
            for query in args.query:
                exchange.clear()
                retrieval = pipeline.search(query)
                prompt = build_prompt(
                    query,
                    retrieval,
                    settings.generation_context_size,
                    settings.generation_output_tokens,
                    settings.generation_template_reserve,
                    settings.generation_source_bytes,
                )

                service.retriever = Snapshot(retrieval)
                answer = service.ask(query)
                row = {
                    "query": query,
                    "prompt": asdict(prompt),
                    "response": asdict(answer),
                    "exchange": dict(exchange),
                }
                results.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
