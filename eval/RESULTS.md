# Exploratory pilot

Questions: 10; user-authored: 0; common documents: 60.

Metric unit: annotated source passage; a hit must cover >=80% of its characters.
Recall@5 is evidence-unit recall, not a count of all overlapping chunks.
MRR is the mean reciprocal rank of the first supporting hit in the returned top five.
Multi-hop recall counts required passages; MRR is not joint reasoning accuracy.

| Strategy | Evidence recall@5 | MRR@5 |
|---|---:|---:|
| fixed | 0.7000 | 0.3583 |
| recursive | 0.9500 | 0.6250 |
| semantic | 0.8000 | 0.4500 |
| sentence_window | 0.7000 | 0.3233 |
| late | 0.4000 | 0.2700 |

Seed-only pilot labels are exploratory, not independent proof of generalization.
Thirty user-authored questions remain required for the final 40-question set.
Chunk budgets and passage coverage affect which evidence survives retrieval.
The JSON trace identifies actual recovered/missed passages for causal inspection.
Do not infer a universal winner from this small, source-concentrated sample.
