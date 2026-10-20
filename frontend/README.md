# ChunkForge UI

Angular 22 / TypeScript client for the retrieval workbench. Use Node.js 26.

```sh
npm ci
npm start
```

Run `make api` from the repository root in another terminal. Visit localhost:4200.
The development proxy strips `/api` before forwarding requests to localhost:8000.

Search shows source evidence and independent retrieval scores. Ask displays
citation-linked answers or explicit abstention/degradation. Compare runs two
strategies sequentially to respect the local backend's one-request capacity.

```sh
npm run build
npm test -- --watch=false
```

Production bundle: `dist/chunkforge-ui/browser`. The multi-stage Dockerfile builds
it, then Nginx serves static assets and proxies `/api` to `api:8000`.
Database credentials and direct database access never belong in the browser.
