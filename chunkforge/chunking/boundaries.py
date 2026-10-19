"""Conservative English sentence boundaries; no claim of linguistic completeness."""

import re


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Keep separators in spans and avoid common abbreviations and decimal points.

    This deterministic rule-based splitter is inspectable, but can still mistake
    uncommon abbreviations, citations, or mathematical notation for boundaries.
    """
    ends: list[int] = []
    for match in re.finditer(r'[.!?][\)\]"\u201d\u2019]*\s+(?=\S)', text):
        prefix = text[: match.start() + 1]
        if re.search(r"\b(?:Dr|Mr|Mrs|Ms|Prof|Fig|Eq|vs|etc|al)\.$", prefix, re.IGNORECASE):
            continue
        if re.search(r"(?:\b[A-Z]\.|\be\.g\.|\bi\.e\.)$", prefix):
            continue
        ends.append(match.end())
    ends.append(len(text))
    start = 0
    spans: list[tuple[int, int]] = []
    for end in ends:
        if end > start:
            spans.append((start, end))
        start = end
    return spans
