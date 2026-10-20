/** Application shell; health is liveness, not a claim that models are warmed up. */
import { Component, inject, OnInit, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { ChunkforgeService } from './services/chunkforge.service';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.html',
})
export class App implements OnInit {
  private readonly api = inject(ChunkforgeService);
  readonly apiStatus = signal('Checking API…');
  async ngOnInit(): Promise<void> {
    try {
      await this.api.health();
      this.apiStatus.set('API process available');
    } catch {
      this.apiStatus.set('API unavailable');
    }
  }
}
