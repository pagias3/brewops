# BrewOps

Telemetry app for the office coffee machines. Python 3.11+, FastAPI, SQLite (stdlib `sqlite3`, no ORM). Frontend is vanilla JS with no build step and no dependencies.

## Architecture

`ingest → db → api → frontend`, all in `src/brewops/`:

- `ingest/` — CSV loader (`loader.py`) and CLI (`cli.py`).
- `db/` — `schema.py` (tables, seed machines and drinks), `queries.py` (all SQL), `connection.py` (DB path: `$BREWOPS_DB`, default `./brewops.db`).
- `api/main.py` — FastAPI JSON API under `/api/*`, and serves `frontend/` at `/`. One process, port 8123.
- `frontend/` — `index.html`, `app.js`, `style.css`. `app.js` fetches the API and builds the dashboard and machine cards.

Machine cards: `GET /api/machines/{id}` returns `queries.get_machine_health()` as-is (no response model), so a new key there reaches the card with no API change. `app.js` `renderMachineCards()` renders it.

## Two ways data gets in

Both write `brew_events` (`source` is `csv` or `manual`) and `maintenance_events`.

1. **CSV ingest** — `uv run ingest [path]` loads `data/inbox/` by default. Filename prefix picks the type: `brews_*` (source `csv`), `manual_*` (brews, source `manual`), `maintenance_*`. Invalid rows are skipped and reported, and the rest of the file still loads.
2. **Manual entry** — the dashboard forms call `POST /api/brews` and `POST /api/maintenance`. This is how Old Faithful (`has_telemetry = false`) is logged.

## Run and test

- `uv run seed` — drops and rebuilds the DB from `data/inbox/` (also wipes hand-logged brews). Run from the repo root.
- `uv run start` — serves http://localhost:8123. The server does not auto-reload, so restart it after code changes. A stale process on 8123 serves old code.
- `uv run pytest` — tests in `tests/`, using a temp DB via `BREWOPS_DB` and an in-process ASGI client.
- `uv run scripts/lab_a_check.py` — checks the machine specialty in the health payload against the DB.

## Conventions

- Timestamps are naive local time, stored as `YYYY-MM-DD HH:MM:SS`. The API accepts form styles too, normalizes them, and rejects future timestamps. The loader accepts only the storage format.
- Drinks live only in `DRINK_TYPES` in `db/schema.py` and are applied with `uv run seed`. Never hardcode a drink elsewhere. `name` is the stable key, `label` is display-only, and a `name` is never renamed. Show labels in the UI, never keys. See the `add-drink-type` skill.
- Put SQL in `db/queries.py` and validation in `api/main.py` or `ingest/loader.py`. Keep API responses additive so existing keys stay put.
- The frontend must not reference external URLs (`tests/test_frontend.py` enforces this).
- `.agents/skills/` holds the project skills.
