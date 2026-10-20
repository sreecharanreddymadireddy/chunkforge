/** Wire contracts matching FastAPI's OpenAPI schemas. Scores are not confidence. */
export type Strategy = 'fixed' | 'recursive' | 'semantic' | 'sentence_window' | 'late';
export const STRATEGIES: Strategy[] = ['fixed', 'recursive', 'semantic', 'sentence_window', 'late'];
export interface QueryRequest {
  query: string;
  strategy: Strategy;
}
export interface ChunkResult {
  doc_id: string;
  chunk_index: number;
  text: string;
  char_start: number;
  char_end: number;
  score: number;
  context_text: string | null;
}
export interface RankedHit {
  chunk: ChunkResult;
  dense_score: number | null;
  bm25_score: number | null;
  rrf_score: number | null;
  rerank_score: number | null;
}
export interface SearchResponse {
  query: string;
  strategy: Strategy;
  mode: string;
  reasons: string[];
  hits: RankedHit[];
  milliseconds: number;
}
export interface Citation {
  id: number;
  doc_id: string;
  chunk_index: number;
  char_start: number;
  char_end: number;
  text: string;
  truncated: boolean;
}
export interface Answer {
  status: 'answered' | 'insufficient_evidence' | 'evidence_only';
  answer: string | null;
  citations: Citation[];
  evidence: Citation[];
  retrieval_mode: string;
  reasons: string[];
  model: string;
  prompt_eval_count: number | null;
  eval_count: number | null;
  citation_format: string | null;
}
