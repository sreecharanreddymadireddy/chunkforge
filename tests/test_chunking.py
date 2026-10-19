"""Verify source provenance, Unicode safety, overlap and strategy-specific behavior."""

import math
from dataclasses import FrozenInstanceError

import pytest

from chunkforge.chunking.base import Chunker, TokenCounter
from chunkforge.chunking.boundaries import sentence_spans
from chunkforge.chunking.fixed import FixedChunker
from chunkforge.chunking.late import LateChunker
from chunkforge.chunking.model_backend import BGEBackend, ContextTokens, normalized_mean
from chunkforge.chunking.recursive import RecursiveChunker
from chunkforge.chunking.semantic import SemanticChunker
from chunkforge.chunking.sentence_window import SentenceWindowChunker


class TopicEncoder:
    """Known orthogonal topics make semantic breakpoint expectations deterministic."""

    def encode(self, texts: list[str]) -> list[tuple[float, ...]]:
        """Represent database and cooking sentences with distinct directions."""
        return [(0.0, 2.0) if "Cooking" in text else (2.0, 0.0) for text in texts]


class CharacterEncoder:
    """Contextual test vectors depend on the whole source, not a local chunk."""

    model_name = "deterministic-context-fixture"

    def contextualize(self, text: str) -> list[ContextTokens]:
        """Supply one token per character so pooling math can be calculated by hand."""
        return [
            ContextTokens(
                0,
                len(text),
                tuple((i, i + 1) for i in range(len(text))),
                tuple((float(i + 1), float(len(text))) for i in range(len(text))),
            )
        ]


@pytest.fixture(scope="module")
def counter() -> TokenCounter:
    """Share a single vocabulary instance among fast deterministic tests."""
    return TokenCounter()


def strategies(counter: TokenCounter) -> list[Chunker]:
    """All five algorithms with small budgets to force meaningful boundaries."""
    return [
        FixedChunker(24, 4, counter),
        RecursiveChunker(24, counter),
        SemanticChunker(TopicEncoder(), 24, counter=counter),
        SentenceWindowChunker(1, 24, counter),
        LateChunker(CharacterEncoder(), 24, counter),
    ]


def test_all_strategies_preserve_metadata_and_coverage(counter: TokenCounter) -> None:
    """Every source character is covered; stored text, count and offsets agree."""
    text = "Dr. Rao explains PostgreSQL. Cooking uses fresh beans!\n\n" * 12 + "你好 🧠 café."
    for chunker in strategies(counter):
        chunks = chunker.chunk(text, "paper-1")
        covered: set[int] = set()
        for index, chunk in enumerate(chunks):
            assert chunk.doc_id == "paper-1"
            assert chunk.chunk_index == index
            assert chunk.text == text[chunk.char_start : chunk.char_end]
            assert chunk.token_count == counter.count(chunk.text)
            assert 0 < chunk.token_count <= 24
            assert "\ufffd" not in chunk.text
            covered.update(range(chunk.char_start, chunk.char_end))
        assert covered == set(range(len(text))), type(chunker).__name__


def test_empty_and_single_document_behavior(counter: TokenCounter) -> None:
    """Whitespace produces no searchable chunks; a short sentence produces one."""
    for chunker in strategies(counter):
        assert chunker.chunk(" \n\t", "empty") == []
        assert len(chunker.chunk("Postgres stores vectors.", "short")) == 1


def test_fixed_overlap_and_frozen_record(counter: TokenCounter) -> None:
    """Overlap repeats a bounded source suffix while each new window advances."""
    text = " word" * 100
    chunks = FixedChunker(20, 5, counter).chunk(text, "d")
    assert len(chunks) == 7
    for left, right in zip(chunks, chunks[1:], strict=False):
        assert left.char_start < right.char_start < left.char_end
        assert counter.count(text[right.char_start : left.char_end]) == 5
    with pytest.raises(FrozenInstanceError):
        chunks[0].text = "changed"  # type: ignore[misc]  # Deliberate runtime immutability check.


def test_recursive_prefers_structure_and_falls_back(counter: TokenCounter) -> None:
    """Paragraphs stay together when possible; unpunctuated long text still fits."""
    paragraph = "One paragraph has useful context.\n\n"
    chunks = RecursiveChunker(counter.count(paragraph), counter).chunk(paragraph * 3, "d")
    assert [c.text for c in chunks] == [paragraph] * 3
    long_text = "uninterrupted " * 200
    assert "".join(c.text for c in RecursiveChunker(20, counter).chunk(long_text, "d")) == long_text


def test_semantic_breaks_on_topic_change(counter: TokenCounter) -> None:
    """A known vector direction change causes a boundary even below the size cap."""
    text = "Databases store rows. Postgres uses indexes. Cooking uses beans."
    chunks = SemanticChunker(TopicEncoder(), 512, 0.5, counter).chunk(text, "d")
    assert len(chunks) == 2
    assert chunks[0].text == "Databases store rows. Postgres uses indexes. "
    assert chunks[1].text == "Cooking uses beans."


def test_sentence_window_keeps_anchor_and_context_distinct(counter: TokenCounter) -> None:
    """Middle anchor gets both neighbors; boundary anchors have clipped windows."""
    text = "First sentence. Second sentence. Third sentence."
    chunks = SentenceWindowChunker(1, 512, counter).chunk(text, "d")
    assert len(chunks) == 3
    assert chunks[1].text == "Second sentence. "
    assert chunks[1].context_text == text
    assert chunks[0].context_text == "First sentence. Second sentence. "
    assert chunks[2].context_text == "Second sentence. Third sentence."
    for chunk in chunks:
        assert chunk.context_text == text[chunk.context_start : chunk.context_end]


def test_late_pooling_uses_joint_context_and_retains_vectors(counter: TokenCounter) -> None:
    """Compare the first chunk's actual pooled vector to its hand-calculated mean."""
    text = " word" * 20
    chunks = LateChunker(CharacterEncoder(), 5, counter).chunk(text, "d")
    assert len(chunks) == 4
    first = chunks[0]
    expected = normalized_mean([(float(i + 1), float(len(text))) for i in range(first.char_end)])
    assert first.embedding == pytest.approx(expected)
    assert first.embedding is not None
    assert math.sqrt(sum(x * x for x in first.embedding)) == pytest.approx(1.0)
    assert first.context_start == 0 and first.context_end == len(text)
    assert first.embedding_model == CharacterEncoder.model_name
    shorter = LateChunker(CharacterEncoder(), 5, counter).chunk(" word" * 10, "d")
    assert shorter[0].text == first.text
    assert shorter[0].embedding != first.embedding


def test_common_sentence_edge_cases() -> None:
    """Abbreviations and decimal punctuation should not create obvious false splits."""
    text = "Dr. Rao cites Fig. 2 and value 3.14. Next result!"
    assert [text[a:b] for a, b in sentence_spans(text)] == [
        "Dr. Rao cites Fig. 2 and value 3.14. ",
        "Next result!",
    ]


def test_invalid_limits_and_vectors(counter: TokenCounter) -> None:
    """Fail immediately rather than looping forever or returning malformed vectors."""
    with pytest.raises(ValueError):
        FixedChunker(10, 10, counter)
    with pytest.raises(ValueError):
        RecursiveChunker(0, counter)
    with pytest.raises(ValueError):
        SentenceWindowChunker(-1, 20, counter)
    with pytest.raises(ValueError):
        SemanticChunker(TopicEncoder(), threshold=2)
    with pytest.raises(ValueError):
        LateChunker(CharacterEncoder(), 0, counter)
    invalid_vectors: list[list[tuple[float, ...]]] = [[], [(0.0, 0.0)], [(1.0,), (1.0, 2.0)]]
    for vectors in invalid_vectors:
        with pytest.raises(ValueError):
            normalized_mean(vectors)


def test_literal_special_tokens_and_unicode(counter: TokenCounter) -> None:
    """Source markers are ordinary text; multi-byte characters are never corrupted."""
    text = "<|endoftext|> 你好 🧠 café " * 20
    chunks = FixedChunker(16, 0, counter).chunk(text, "d")
    assert "".join(c.text for c in chunks) == text
    assert all(c.token_count <= 16 for c in chunks)
    with pytest.raises(ValueError):
        counter.prefix_end("🧠", 1)


def test_model_tokenizer_partitions_without_source_loss() -> None:
    """Embedding tokenizer limits are independent of the reporting tokenizer."""

    class WordTokenizer:
        """A tiny offset-producing tokenizer models a different vocabulary."""

        def __call__(
            self,
            text: str,
            add_special_tokens: bool,
            return_offsets_mapping: bool,
            verbose: bool,
        ) -> dict[str, list[tuple[int, int]]]:
            """Return one token per non-whitespace word."""
            import re

            return {"offset_mapping": [m.span() for m in re.finditer(r"\S+", text)]}

    backend = BGEBackend.__new__(BGEBackend)
    backend.tokenizer = WordTokenizer()
    backend.limit = 2
    text = "  one two three four five  "
    spans = backend._passages(text)
    assert len(spans) == 3
    assert "".join(text[a:b] for a, b in spans) == text
    assert all(len(text[a:b].split()) <= 2 for a, b in spans)
    assert backend._passages("  ") == []


def test_late_rejects_missing_context_coverage(counter: TokenCounter) -> None:
    """A provider cannot silently omit or truncate the source tail."""

    class MissingTailEncoder:
        """Invalid provider intentionally omits the final source character."""

        model_name = "invalid-fixture"

        def contextualize(self, text: str) -> list[ContextTokens]:
            """Return a partial passage so completeness validation must fail."""
            return [ContextTokens(0, 1, ((0, 1),), ((1.0, 1.0),))]

    with pytest.raises(ValueError, match="full source"):
        LateChunker(MissingTailEncoder(), 20, counter).chunk("two", "d")


def test_model_passages_recheck_tokenization_after_cut() -> None:
    """Regression: an isolated prefix can tokenize longer than its parent token slice."""

    class RetokenizingTokenizer:
        """Simulate boundary-sensitive tokenization without model downloads."""

        def __call__(
            self,
            text: str,
            add_special_tokens: bool,
            return_offsets_mapping: bool,
            verbose: bool,
        ) -> dict[str, list[tuple[int, int]]]:
            """Make the first proposed prefix exceed its original two-token budget."""
            import re

            if text.strip() == "one two":
                return {"offset_mapping": [(0, 1), (1, 3), (4, 7)]}
            return {"offset_mapping": [m.span() for m in re.finditer(r"\S+", text)]}

    backend = BGEBackend.__new__(BGEBackend)
    backend.tokenizer = RetokenizingTokenizer()
    backend.limit = 2
    text = "one two three"
    spans = backend._passages(text)
    assert [text[a:b] for a, b in spans] == ["one ", "two ", "three"]


def test_late_whitespace_tail_does_not_exceed_budget(counter: TokenCounter) -> None:
    """Regression: a tokenless trailing space must not turn a full core into cap+1."""

    class WhitespaceAwareEncoder(CharacterEncoder):
        """Like BGE, emit no token vector for standalone whitespace."""

        def contextualize(self, text: str) -> list[ContextTokens]:
            """Retain vectors only for non-whitespace source characters."""
            passage = super().contextualize(text)[0]
            pairs = [
                (offset, v)
                for offset, v in zip(
                    passage.offsets,
                    passage.vectors,
                    strict=True,
                )
                if text[offset[0] : offset[1]].strip()
            ]
            return [
                ContextTokens(
                    0,
                    len(text),
                    tuple(p[0] for p in pairs),
                    tuple(p[1] for p in pairs),
                )
            ]

    text = " word" * 20 + " "
    chunks = LateChunker(WhitespaceAwareEncoder(), 5, counter).chunk(text, "d")
    assert "".join(c.text for c in chunks) == text
    assert all(c.token_count <= 5 for c in chunks)
    assert all(c.embedding is not None for c in chunks)
