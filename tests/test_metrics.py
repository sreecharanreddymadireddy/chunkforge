"""Worked metric examples, duplicate handling and source-span relevance matching."""

import pytest

from chunkforge.eval.metrics import recall_at_k, reciprocal_rank
from chunkforge.eval.run_eval import hit_labels
from chunkforge.store.pgvector_store import SearchResult


def test_recall_and_reciprocal_rank_worked_examples() -> None:
    """Two of three evidence units recovered gives 2/3 recall; first at rank two gives 1/2 RR."""
    ranked = [set(), {"a"}, {"a", "b"}, set()]
    assert recall_at_k(ranked, {"a", "b", "c"}) == pytest.approx(2 / 3)
    assert reciprocal_rank(ranked, {"a", "b", "c"}) == 0.5
    assert recall_at_k([], {"a"}) == reciprocal_rank([], {"a"}) == 0
    with pytest.raises(ValueError):
        recall_at_k([], set())
    with pytest.raises(ValueError):
        reciprocal_rank([], set())


def test_source_coverage_and_document_identity() -> None:
    """Prevent matching the wrong source or a tiny accidental span overlap."""
    hit = SearchResult("paper", 0, "x" * 80, 0, 80, 0.5, None)
    assert hit_labels(hit, [("gold", "paper", 0, 100)]) == {"gold"}
    assert hit_labels(hit, [("gold", "other", 0, 100)]) == set()
    assert hit_labels(hit, [("gold", "paper", 60, 100)]) == set()
