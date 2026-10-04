# CivAlpha frontend

Angular (standalone components, signals, hand-written SVG charts). Talks only to the backend under `/api`.

* Production: built and served by nginx in `Dockerfile` (`docker compose up` from the repo root).
* Development: `npm ci && npx ng serve` (proxies `/api` to `http://localhost:8080` via `proxy.conf.json`).
  Angular CLI 22 needs Node.js >= 22.22.3.
