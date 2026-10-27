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

**Week 3b (waivers, API, first UI): done.** Free agents are ranked for my team two ways.
*Stream* re-runs this week's matchup with each free agent swapped in for one of my
lowest-value players and ranks by the change in win odds; *hold* ranks by the change in
expected categories won per week against a league-average team over the rest of the
season. A category board labels each category locked / swing / lost. A FastAPI service
serves matchups and waivers, and a Next.js app has Matchup and Waivers pages. The Matchup
page also breaks the week down by player and day, following the lineup set on ESPN (box
scores for days played, projections after), and a Refresh button runs the ingest
(`POST /api/refresh`).
Still open: what the league's add limit means and when a pickup can first play (the
rankings assume the next day; see PROJECT_PLAN.md).

**Week 3c (live scoring): in progress.** A Refresh during games is live: totals so far
include today's lines, games that have tipped are locked into their set slots, and only the
rest of each live game is projected (see *Live scoring* below).

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

## Frontend quickstart

Requires Node 20.9+. With `fh-api` running:

```bash
cd frontend
npm install
npm run dev                   # http://localhost:3000; /api/* is proxied to the backend
npm run build && npm run typecheck
```

Set `API_URL` if the backend isn't at `http://127.0.0.1:8000`.

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
                     opponent factors, dispersion fit, matchup simulation, waiver rankings
    context.py       stored snapshot -> analytics inputs (rates, games, priorities)
    predict.py       current-week predictions from stored data + `fh-predict`
    waivers.py       stream/hold rankings for one team + `fh-waivers`
    api/             FastAPI app (`fh-api`)
  alembic/           migrations
  data/              per-season matchup calendars
  tests/
frontend/            Next.js + TanStack Query: Matchup and Waivers pages
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
- **Live scoring**: an ingest (or Refresh) during games is live. ESPN's matchup totals only
  count a day once it's processed, so "now" totals are summed from box scores instead: started
  games (not BE/IR) on completed days plus today's lines so far (`live_game_stats`). Game states
  come from ESPN's public NBA scoreboard (`live_games`; estimated from tip times if it's down). A
  game that has tipped is locked into the player's set slot and only the rest of it is projected;
  a free agent can't be added to a game already under way. Each ingest re-fetches the week's
  completed days for stat corrections and warns if ESPN's totals disagree with the box scores.
- **Set lineups**: ESPN keeps a lineup for every future day, not just today. Each ingest stores
  every team's slots from today through the end of the week (`daily_lineups`); past days' slots
  come from the box scores. Win odds still assume the best lineup each day, so the by-day view's
  totals can differ from the odds' projected finals.
- **History tables** are never wiped: `player_game_stats` (daily box scores; missing days are
  backfilled, and the current week's days are re-fetched for stat corrections) and `matchup_predictions`
  (one row per matchup per run, for tracking odds through the week and for backtests).
- **Opponent stats** come from ESPN's public, undocumented site API and may break without notice;
  a failed fetch keeps the last stored set and is logged as a warning.
