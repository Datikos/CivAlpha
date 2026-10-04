# CivAlpha frontend

Angular (standalone components, signals, hand-written SVG charts). Talks only to the Python API under `/api`.

* Production: built and served by nginx in `Dockerfile` (`docker compose up` from the repo root).
* Development: `npm ci && npx ng serve` (proxies `/api` to the API at `http://localhost:8000` via `proxy.conf.json`; run it with `cd backend && uvicorn civalpha.platform.app:app`).
  Angular CLI 22 needs Node.js >= 22.22.3.
