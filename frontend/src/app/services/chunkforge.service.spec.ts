import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ChunkforgeService } from './chunkforge.service';

describe('ChunkforgeService', () => {
  it('sends the typed query and strategy without changing question meaning', async () => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    const service = TestBed.inject(ChunkforgeService);
    const pending = service.search({ query: 'DivOPD', strategy: 'recursive' });
    const http = TestBed.inject(HttpTestingController);
    const request = http.expectOne('/api/search');
    expect(request.request.body).toEqual({ query: 'DivOPD', strategy: 'recursive' });
    request.flush({
      query: 'DivOPD',
      strategy: 'recursive',
      mode: 'hybrid',
      reasons: [],
      hits: [],
      milliseconds: 3,
    });
    expect((await pending).mode).toBe('hybrid');
    http.verify();
  });
});
