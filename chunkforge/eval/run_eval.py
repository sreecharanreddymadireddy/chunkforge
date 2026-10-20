"""Compare every strategy on one shared document set; seed-only runs are explicitly pilots."""

import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Literal, cast

from pydantic import BaseModel, Field

from chunkforge.config import get_settings
from chunkforge.eval.metrics import recall_at_k, reciprocal_rank
from chunkforge.retrieval.runtime import SearchRuntime
from chunkforge.store.pgvector_store import STRATEGIES, SearchResult


class EvidenceLabel(BaseModel):
    """An exact quote locates the same source evidence across different chunk boundaries."""

    doc_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    text: str = Field(min_length=20)


class Question(BaseModel):
    """Seeds are exploratory; user-authored labels are required before a final benchmark."""

    id: str
    source: Literal["seed", "user"]
    category: str
    question: str = Field(min_length=5)
    evidence: list[EvidenceLabel] = Field(min_length=1)


def hit_labels(hit: SearchResult, labels: list[tuple[str, str, int, int]]) -> set[str]:
    """Count an evidence unit when a hit covers at least 80% of its annotated characters."""
    return {
        key
        for key, doc_id, start, end in labels
        if hit.doc_id == doc_id
        and max(0, min(end, hit.char_end) - max(start, hit.char_start)) / (end - start) >= 0.8
    }


def main() -> None:
    """Write measured scores, source hashes and per-question hit traces."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=Path, default=Path(__file__).with_name("qa_pairs.json"))
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--documents", type=int, default=75)
    parser.add_argument("--output", type=Path, default=Path("eval/RESULTS.md"))
    args = parser.parse_args()
    questions = [Question.model_validate(q) for q in json.loads(args.pairs.read_text())]
    if not questions or len({q.id for q in questions}) != len(questions) or args.documents < 1:
        parser.error("Require questions with unique IDs and positive document count")
    user_count = sum(q.source == "user" for q in questions)
    if not args.pilot and (len(questions) < 40 or user_count < 30):
        parser.error("Final evaluation requires 40 labels including 30 user questions; use --pilot")
    settings = get_settings()
    labels: dict[str, list[tuple[str, str, int, int]]] = {}
    hashes: dict[str, str] = {}
    for question in questions:
        labels[question.id] = []
        for i, label in enumerate(question.evidence):
            text = (settings.processed_data_dir / (label.doc_id + ".txt")).read_text()
            start = text.find(label.text)
            if start < 0:
                parser.error(f"Evidence not found: {question.id}/{label.doc_id}")
            labels[question.id].append(
                (f"{question.id}:{i}", label.doc_id, start, start + len(label.text))
            )
            hashes[label.doc_id] = hashlib.sha256(text.encode()).hexdigest()
    runtime = SearchRuntime.create(settings)
    try:
        document_sets = [
            set(
                c.doc_id
                for c in runtime.store.lexical_snapshot(
                    strategy, runtime.embedder.model_id, runtime.embedder.representation(strategy)
                )
            )
            for strategy in STRATEGIES
        ]
        common = sorted(set.intersection(*document_sets))[: args.documents]
        if len(common) < args.documents and not args.pilot:
            parser.error("Full requested corpus is not indexed across every strategy")
        if not common or not set(hashes).issubset(common):
            parser.error("Labeled sources must exist in the common strategy corpus")
        measurements = []
        for strategy in STRATEGIES:
            pipeline = runtime.pipeline(strategy, common)
            rows = []
            for question in questions:
                started = perf_counter()
                response = pipeline.search(question.question)
                if response.mode != "hybrid":
                    raise RuntimeError("Refusing to compare silently degraded retrieval")
                ranked = [hit_labels(h.chunk, labels[question.id]) for h in response.hits]
                relevant = {label[0] for label in labels[question.id]}
                rows.append(
                    {
                        "id": question.id,
                        "recall_at_5": recall_at_k(ranked, relevant),
                        "reciprocal_rank": reciprocal_rank(ranked, relevant),
                        "milliseconds": round((perf_counter() - started) * 1000, 3),
                        "hits": [
                            {
                                "doc_id": h.chunk.doc_id,
                                "chunk_index": h.chunk.chunk_index,
                                "matched_labels": sorted(matches),
                            }
                            for h, matches in zip(response.hits, ranked, strict=True)
                        ],
                    }
                )
            item = {
                "strategy": strategy,
                "recall_at_5": mean(cast(float,r["recall_at_5"]) for r in rows),
                "mrr": mean(cast(float,r["reciprocal_rank"]) for r in rows),
                "questions": rows,
            }
            measurements.append(item)
            print(json.dumps(item), flush=True)
        report = {
            "pilot": args.pilot,
            "question_count": len(questions),
            "user_questions": user_count,
            "documents": common,
            "source_hashes": hashes,
            "model": runtime.embedder.model_id,
            "measurements": measurements,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
        heading = "Exploratory pilot" if args.pilot else "Retrieval evaluation"
        lines = [
            f"# {heading}",
            "",
            f"Questions: {len(questions)}; user-authored: {user_count}; "
            f"common documents: {len(common)}.",
            "",
            "Metric unit: annotated source passage; a hit must cover >=80% of its characters.",
            "Recall@5 is evidence-unit recall, not a count of all overlapping chunks.",
            "MRR is the mean reciprocal rank of the first supporting hit in the returned top five.",
            "Multi-hop recall counts required passages; MRR is not joint reasoning accuracy.",
            "",
            "| Strategy | Evidence recall@5 | MRR@5 |",
            "|---|---:|---:|",
        ]
        for m in measurements:
            lines.append(f"| {m['strategy']} | {m['recall_at_5']:.4f} | {m['mrr']:.4f} |")
        lines += [
            "",
            "Seed-only pilot labels are exploratory, not independent proof of generalization.",
            "Thirty user-authored questions remain required for the final 40-question set.",
            "Chunk budgets and passage coverage affect which evidence survives retrieval.",
            "The JSON trace identifies actual recovered/missed passages for causal inspection.",
            "Do not infer a universal winner from this small, source-concentrated sample.",
        ]
        args.output.write_text("\n".join(lines) + "\n")
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
