/** Query -> typed HTTP request -> evidence or citation-linked generated answer. */
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe } from '@angular/common';
import { Answer, SearchResponse, Strategy, STRATEGIES } from '../../models/api.models';
import { ChunkforgeService, errorMessage } from '../../services/chunkforge.service';
import { EvidenceComponent } from './evidence';

@Component({
  selector: 'app-search',
  imports: [FormsModule, EvidenceComponent, DecimalPipe],
  template: `
    <header class="page-heading">
      <span class="eyebrow">RETRIEVAL WORKBENCH</span>
      <h1>Find the evidence.<br />Inspect the answer.</h1>
      <p>Search your paper corpus by meaning and keywords, with every ranking stage visible.</p>
    </header>
    <form class="query-form" (ngSubmit)="run()">
      <label for="question">Your question</label>
      <textarea
        id="question"
        name="query"
        [(ngModel)]="query"
        rows="3"
        maxlength="2000"
        required
      ></textarea>
      <div class="form-row">
        <label
          >Chunking strategy<select name="strategy" [(ngModel)]="strategy">
            @for (s of strategies; track s) {
              <option [value]="s">{{ s }}</option>
            }
          </select></label
        >
        <label
          >Operation<select name="operation" [(ngModel)]="operation">
            <option value="search">Retrieve evidence</option>
            <option value="ask">Ask with citations</option>
          </select></label
        >
        <button type="submit" [disabled]="busy() || !query.trim()">
          {{ busy() ? 'Working…' : 'Run query' }}
        </button>
      </div>
    </form>
    @if (busy()) {
      <p class="notice" role="status">
        Running locally. The first request loads models; generation can take longer on CPU.
      </p>
    }
    @if (error()) {
      <p class="warning" role="alert">{{ error() }}</p>
    }
    @if (result(); as r) {
      <div class="result-heading">
        <h2>Retrieved evidence</h2>
        <span>{{ r.mode }} · {{ r.milliseconds | number: '1.0-0' }} ms</span>
      </div>
      @if (r.reasons.length) {
        <p class="warning">Reduced capability: {{ r.reasons.join(', ') }}</p>
      }
      <app-evidence [hits]="r.hits" />
    }
    @if (answer(); as a) {
      <section class="answer">
        <div class="result-heading">
          <h2>{{ a.status === 'answered' ? 'Grounded answer' : 'Answer status' }}</h2>
          <span>{{ a.status }}</span>
        </div>
        <p class="source-text">
          {{
            a.answer ||
              'Generation unavailable or invalid. Retrieved evidence is still available below.'
          }}
        </p>
        <small>{{ a.model }} · {{ a.retrieval_mode }}</small>
        @if (a.reasons.length) {
          <p class="warning">{{ a.reasons.join(', ') }}</p>
        }
        <h3>Evidence supplied to the model</h3>
        @for (c of a.evidence; track c.id) {
          <details>
            <summary>[{{ c.id }}] {{ c.doc_id }} · chunk {{ c.chunk_index }}</summary>
            <p class="source-text">{{ c.text }}</p>
            <small
              >Characters [{{ c.char_start }}, {{ c.char_end }}).
              {{ c.truncated ? 'Budgeted excerpt.' : 'Complete core.' }}</small
            >
          </details>
        }
      </section>
    }
  `,
})
export class SearchComponent {
  private readonly api = inject(ChunkforgeService);
  readonly strategies = STRATEGIES;
  query = 'How does DivOPD select turns for training?';
  strategy: Strategy = 'recursive';
  operation: 'search' | 'ask' = 'search';
  readonly busy = signal(false);
  readonly error = signal('');
  readonly result = signal<SearchResponse | null>(null);
  readonly answer = signal<Answer | null>(null);
  async run(): Promise<void> {
    this.busy.set(true);
    this.error.set('');
    this.result.set(null);
    this.answer.set(null);
    const request = { query: this.query.trim(), strategy: this.strategy };
    try {
      if (this.operation === 'ask') this.answer.set(await this.api.ask(request));
      else this.result.set(await this.api.search(request));
    } catch (error) {
      this.error.set(errorMessage(error));
    } finally {
      this.busy.set(false);
    }
  }
}
