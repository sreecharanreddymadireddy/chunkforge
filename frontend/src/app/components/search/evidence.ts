/** One reusable evidence renderer for search and side-by-side strategy comparison. */
import { Component, input } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { RankedHit } from '../../models/api.models';

@Component({
  selector: 'app-evidence',
  imports: [DecimalPipe],
  template: `
    @for (hit of hits(); track hit.chunk.doc_id + ':' + hit.chunk.chunk_index; let i = $index) {
      <article class="evidence">
        <div class="evidence-meta">
          <span class="rank">{{ i + 1 }}</span> <strong>{{ hit.chunk.doc_id }}</strong
          ><span>Chunk {{ hit.chunk.chunk_index }}</span>
        </div>
        <p class="source-text evidence-text" tabindex="0" aria-label="Retrieved passage">
          {{ hit.chunk.text }}
        </p>
        <details>
          <summary>Inspect scores and source range</summary>
          <dl class="scores">
            <div>
              <dt>Cosine</dt>
              <dd>
                {{
                  hit.dense_score === null ? 'Not retrieved' : (hit.dense_score | number: '1.4-4')
                }}
              </dd>
            </div>
            <div>
              <dt>BM25</dt>
              <dd>
                {{ hit.bm25_score === null ? 'Not retrieved' : (hit.bm25_score | number: '1.4-4') }}
              </dd>
            </div>
            <div>
              <dt>RRF</dt>
              <dd>
                {{ hit.rrf_score === null ? 'Not fused' : (hit.rrf_score | number: '1.6-6') }}
              </dd>
            </div>
            <div>
              <dt>Reranker logit</dt>
              <dd>
                {{
                  hit.rerank_score === null ? 'Not reranked' : (hit.rerank_score | number: '1.4-4')
                }}
              </dd>
            </div>
          </dl>
          <small
            >Characters [{{ hit.chunk.char_start }}, {{ hit.chunk.char_end }}). Scores use different
            scales; none is a confidence percentage.</small
          >
        </details>
      </article>
    } @empty {
      <p class="empty">No matching evidence returned.</p>
    }
  `,
})
export class EvidenceComponent {
  readonly hits = input.required<RankedHit[]>();
}
