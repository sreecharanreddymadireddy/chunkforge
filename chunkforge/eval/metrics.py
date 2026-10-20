"""Evidence-unit recall and reciprocal rank without strategy-dependent chunk denominators."""


def recall_at_k(ranked: list[set[str]], relevant: set[str], k: int = 5) -> float:
    """Fraction of annotated evidence units covered by any of the first k hits."""
    if k < 1 or not relevant:
        raise ValueError("Require positive k and at least one relevant evidence unit")
    found = set().union(*ranked[:k]) if ranked else set()
    return len(found & relevant) / len(relevant)


def reciprocal_rank(ranked: list[set[str]], relevant: set[str]) -> float:
    """Reciprocal position of the first hit covering an annotated evidence unit; zero on miss."""
    if not relevant:
        raise ValueError("At least one relevant evidence unit is required")
    return next((1 / rank for rank, ids in enumerate(ranked, 1) if ids & relevant), 0.0)
