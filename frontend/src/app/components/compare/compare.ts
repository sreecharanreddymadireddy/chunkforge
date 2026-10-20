/** Compare strategies sequentially to respect the local backend's single inference slot. */
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { SearchResponse, Strategy, STRATEGIES } from '../../models/api.models';
import { ChunkforgeService, errorMessage } from '../../services/chunkforge.service';
import { EvidenceComponent } from '../search/evidence';

@Component({
  selector: 'app-compare',
  imports: [FormsModule, EvidenceComponent],
  template: `
    <header class="page-heading">
      <span class="eyebrow">CHUNKING LAB</span>
      <h1>Same question.<br />Different boundaries.</h1>
      <p>
        Compare retrieved passages, not just chunk counts. Scores are diagnostic—not quality
        metrics.
      </p>
    </header>
    <form class="query-form" (ngSubmit)="run()">
      <label for="compare-question">Question</label>
      <textarea
        id="compare-question"
        name="query"
        [(ngModel)]="query"
        rows="2"
        maxlength="2000"
        required
      ></textarea>
      <div class="form-row">
        <label
          >Left strategy<select name="left" [(ngModel)]="left">
            @for (s of strategies; track s) {
              <option [value]="s">{{ s }}</option>
            }
          </select></label
        >
        <label
          >Right strategy<select name="right" [(ngModel)]="right">
            @for (s of strategies; track s) {
              <option [value]="s">{{ s }}</option>
            }
          </select></label
        >
        <button [disabled]="busy() || !query.trim()">
          {{ busy() ? 'Comparing…' : 'Compare evidence' }}
        </button>
      </div>
    </form>
    @if (busy()) {
      <p role="status" class="notice">Searching sequentially to keep local model memory bounded.</p>
    }
    @if (error()) {
      <p role="alert" class="warning">{{ error() }}</p>
    }
    <div class="comparison">
      @for (result of results(); track result.strategy) {
        <section>
          <div class="result-heading">
            <h2>{{ result.strategy }}</h2>
            <span>{{ result.mode }}</span>
          </div>
          @if (result.reasons.length) {
            <p class="warning">{{ result.reasons.join(', ') }}</p>
          }
          <app-evidence [hits]="result.hits" />
        </section>
      }
    </div>
  `,
})
export class CompareComponent {
  private readonly api = inject(ChunkforgeService);
  readonly strategies = STRATEGIES;
  query = 'In QiYao-M, how does Endo-Modality Proxy Training work?';
  left: Strategy = 'fixed';
  right: Strategy = 'recursive';
  readonly busy = signal(false);
  readonly error = signal('');
  readonly results = signal<SearchResponse[]>([]);
  async run(): Promise<void> {
    this.busy.set(true);
    this.error.set('');
    this.results.set([]);
    try {
      const a = await this.api.search({ query: this.query.trim(), strategy: this.left });
      this.results.set([a]);
      if (this.left !== this.right)
        this.results.set([
          a,
          await this.api.search({ query: this.query.trim(), strategy: this.right }),
        ]);
    } catch (error) {
      this.error.set(errorMessage(error));
    } finally {
      this.busy.set(false);
    }
  }
}
