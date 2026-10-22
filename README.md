# Fantasy Helper

Analytics and an AI assistant for an ESPN 10-team, 9-category head-to-head NBA fantasy league:
waiver recommendations, trade analysis, matchup scouting and league-wide category views.

See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the roadmap.

## Status

**Week 1 (data foundation): done.** ESPN league data is ingested into a relational schema:
teams, rosters, players, per-window stat lines (season / last 7 / 15 / 30 / projected),
the NBA schedule by scoring period, and all league matchups.

**Week 2 (analytics core): done.** Per-category player z-scores over any stat window
(FG%/FT% as volume-weighted impact, TO reversed, baseline = rostered players), and team
profiles: per-game category totals, league ranks and inferred punts.

## Backend quickstart

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is installed by uv automatically.

```bash
cd backend
uv sync
uv run alembic upgrade head   # create tables (SQLite at backend/fantasy.db by default)
uv run fh-ingest              # pull the league from ESPN
uv run pytest                 # tests
uv run ruff check . && uv run ruff format --check .
```

Configuration lives in `backend/src/fantasy_helper/config.py` and can be overridden with
`FH_*` env vars or `backend/.env` (see `backend/.env.example`). To use Postgres instead of
SQLite, run `docker compose up -d` and set `FH_DATABASE_URL`.

## Layout

```
backend/
  src/fantasy_helper/
    config.py        settings (FH_* env vars)
    categories.py    scoring categories, read from ESPN league settings
    calendar.py      matchup week <-> scoring period (day) mapping
    domain.py        ESPN-independent snapshot types
    espn/            ESPN fetch (client.py) and pure parsing (parse.py)
    db/              SQLAlchemy models + session
    ingest/          snapshot persistence + `fh-ingest` job
    analytics/       pure functions: player z-scores, team category profiles
  alembic/           migrations
  data/              per-season matchup calendars
  tests/
```

## Data notes

- **Categories**: FG%, FT%, 3PM, REB, AST, STL, BLK, TO (lower is better), PTS. Percentages are
  never stored; they are always derived from makes and attempts so team totals are correct.
- **Matchup calendar**: ESPN's API does not expose matchup date ranges, and this season's weeks are
  irregular (week 1 is 6 days, week 18 is a 14-day All-Star double week, playoff rounds are 2 weeks).
  The calendar is checked in at `backend/data/matchup_calendar_2027.json` and every ingest cross-checks
  it against the scoring periods ESPN has recorded; mismatches show up in `ingest_runs.warnings`.
- **NBA schedule**: comes from ESPN player data. Teams show 80 games, not 82, until the NBA Cup
  fills in the last two; nightly ingests pick them up.
