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

**Week 3a (live matchup predictor): done.** Each ingest predicts every matchup in the current
week: actual totals so far plus a Monte Carlo of the days left (blended per-game rates,
NBA opponent adjustment, injury status, the 10-starter daily lineup cap). Results are
appended to `matchup_predictions`; `fh-predict` prints them on demand.

## Backend quickstart

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is installed by uv automatically.

```bash
cd backend
uv sync
uv run alembic upgrade head   # create tables (SQLite at backend/fantasy.db by default)
uv run fh-ingest              # pull the league from ESPN (run daily, before the first tip-off)
uv run fh-predict             # this week's matchup odds; --team ID for detail (default: mine)
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
    analytics/       pure functions: z-scores, team profiles, blended rates, lineup cap,
                     opponent factors, dispersion fit, matchup simulation
    predict.py       current-week predictions from stored data + `fh-predict`
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
- **Ingest timing**: run once a day before the first game. ESPN's live matchup totals include
  games in progress, which the predictor would also project; a run after the first tip-off is
  flagged in `ingest_runs.warnings` (and on that run's predictions) but not corrected.
- **History tables** are never wiped: `player_game_stats` (daily box scores; missing days are
  backfilled and the latest day is re-fetched for stat corrections) and `matchup_predictions`
  (one row per matchup per run, for tracking odds through the week and for backtests).
- **Opponent stats** come from ESPN's public, undocumented site API and may break without notice;
  a failed fetch keeps the last stored set and is logged as a warning.
