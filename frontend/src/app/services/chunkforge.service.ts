/** Typed REST boundary; Angular never computes vectors or retrieval rankings. */
import { inject, Injectable } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { firstValueFrom, timeout } from 'rxjs';
import { Answer, QueryRequest, SearchResponse } from '../models/api.models';

@Injectable({ providedIn: 'root' })
export class ChunkforgeService {
  private readonly http = inject(HttpClient);
  search(request: QueryRequest): Promise<SearchResponse> {
    return firstValueFrom(
      this.http.post<SearchResponse>('/api/search', request).pipe(timeout(240000)),
    );
  }
  ask(request: QueryRequest): Promise<Answer> {
    return firstValueFrom(this.http.post<Answer>('/api/ask', request).pipe(timeout(240000)));
  }
  health(): Promise<{ status: string }> {
    return firstValueFrom(this.http.get<{ status: string }>('/api/health').pipe(timeout(5000)));
  }
}

export function errorMessage(error: unknown): string {
  if (error instanceof HttpErrorResponse) {
    if (error.status === 429) return 'The local model is busy. Please try again shortly.';
    if (error.status === 0) return 'Cannot reach the API. Check that make api is running.';
    if (typeof error.error?.detail === 'string') return error.error.detail;
  }
  return 'The request did not complete. Check the backend and try again.';
}
