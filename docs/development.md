# Development

Running the backend and frontend locally. Commands start from the repository root. For what the app does and how it works, see the [README](../README.md); for the AWS and Vercel deploy, see [deploy.md](deploy.md).

## Backend quickstart

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is installed by uv automatically.

```bash
cd backend
uv sync
uv run alembic upgrade head   # create tables (SQLite at backend/fantasy.db by default)
uv run fh-ingest              # pull the league from ESPN (any time; live during games)
uv run fh-predict             # this week's matchup odds; --team ID for detail (default: mine)
uv run fh-waivers             # stream and hold rankings; --today if pickups can play today
uv run fh-api                 # REST API on http://127.0.0.1:8000 (docs at /docs)
uv run pytest                 # tests
uv run ruff check . && uv run ruff format --check .
```

Configuration lives in `backend/src/fantasy_helper/config.py` and can be overridden with
`FH_*` env vars or `backend/.env` (see `backend/.env.example`). To use Postgres instead of
SQLite, run `docker compose up -d` and set `FH_DATABASE_URL`.

[backend/README.md](../backend/README.md) covers the data flow, a guide to the modules, the REST API, the commands, the settings, the database and the tests.

## Frontend quickstart

Requires Node 20.9+. With `fh-api` running:

```bash
cd frontend
npm install
npm run dev                   # http://localhost:3000; /api/* is proxied to the backend
npm run build && npm run typecheck
```

Set `API_URL` if the backend isn't at `http://127.0.0.1:8000`.

[frontend/README.md](../frontend/README.md) covers how the app calls the API, what each page shows, the URL params, the components and the conventions.
