# Backend

The Python 3.12 package `fantasy_helper` does four jobs. It pulls the league from ESPN, stores it with SQLAlchemy (SQLite locally, Postgres when deployed), runs the analytics on the stored copy and serves the results over a FastAPI REST API.

Setup, quickstart commands and the AWS deploy are in the [root README](../README.md). This file maps the code. Each module opens with a docstring that explains its method, so this README points to those docstrings rather than repeating them.

## Data flow

```
                 fh-ingest / POST /api/refresh / hourly Lambda
                                   |
espn/client.py --> espn/parse.py --> domain.py (LeagueSnapshot)
                                   |
                         ingest/store.py --> database <-- db/models.py
                                   |              |
                  predict.py (matchup_predictions)   db/queries.py --> domain types
                                                  |
                                       context.py (LeagueContext)
                                                  |
                                       analytics/* (pure functions)
                                                  |
                         predict.py · waivers.py · trades.py --> api/app.py
```

1. **Ingest** ([`ingest/run.py`](src/fantasy_helper/ingest/run.py)).
   - `fetch_snapshot` fetches one consistent `LeagueSnapshot` from ESPN, plus NBA opponent stats from ESPN's public site API.
   - It cross-checks the [matchup calendar](src/fantasy_helper/calendar.py) and ESPN's totals, and stores the snapshot in one transaction.
   - It then appends this week's matchup predictions. A failed prediction step is recorded as a warning, and the ingest still succeeds.
   - Each run is a row in `ingest_runs`, with its counts and warnings.
   - On Postgres, an advisory lock keeps two ingests from overlapping. If one is already running, the second raises `IngestBusy`.
2. **Store** ([`ingest/store.py`](src/fantasy_helper/ingest/store.py)). Season-scoped tables are deleted and rewritten. History tables are upserted and never wiped (see *Database* below).
3. **Read back** ([`db/queries.py`](src/fantasy_helper/db/queries.py)). Stored rows are turned back into [`domain.py`](src/fantasy_helper/domain.py) types, so the analytics never touch ORM rows or `espn_api` objects.
4. **Context** ([`context.py`](src/fantasy_helper/context.py)). `load_context` builds everything the models need:
   - blended per-game rates
   - games left, with opponent factors
   - each player's chance of playing
   - start priorities
   - dispersion
   - totals so far, which are live during games
5. **Analytics** ([`analytics/`](src/fantasy_helper/analytics)). Pure functions over domain types. No database or network access.
6. **Services.** [`predict.py`](src/fantasy_helper/predict.py), [`waivers.py`](src/fantasy_helper/waivers.py) and [`trades.py`](src/fantasy_helper/trades.py) combine a context with the analytics for one team. They return reports that carry their assumptions, so the UI can state them.
7. **API** ([`api/app.py`](src/fantasy_helper/api/app.py)). Serves the services' results, cached in memory per snapshot.

When deployed, [`aws.py`](src/fantasy_helper/aws.py) holds both Lambda entry points: `api` wraps the FastAPI app with Mangum, and `ingest` is the scheduled job. Both are in the same image ([`Dockerfile`](Dockerfile)).

## Module guide

| Module | What it does |
|---|---|
| [`config.py`](src/fantasy_helper/config.py) | `Settings`: `FH_*` env vars, `backend/.env` and, when deployed, the AWS secret |
| [`domain.py`](src/fantasy_helper/domain.py) | ESPN-independent snapshot types: everything downstream consumes these |
| [`categories.py`](src/fantasy_helper/categories.py) | Scoring categories, read from ESPN league settings; percentage cats carry makes and attempts |
| [`calendar.py`](src/fantasy_helper/calendar.py) | Matchup week ↔ scoring period (day), from the checked-in [`data/matchup_calendar_2027.json`](data/matchup_calendar_2027.json) |
| [`espn/client.py`](src/fantasy_helper/espn/client.py) | Network layer: the league via `espn_api`, plus NBA opponent stats and today's scoreboard from ESPN's public site API |
| [`espn/parse.py`](src/fantasy_helper/espn/parse.py) | Pure conversion from ESPN objects and JSON into domain types |
| [`db/models.py`](src/fantasy_helper/db/models.py) | The relational schema, portable across SQLite and Postgres |
| [`db/queries.py`](src/fantasy_helper/db/queries.py) | Stored rows → domain types |
| [`db/session.py`](src/fantasy_helper/db/session.py) | Engine and sessionmaker from `FH_DATABASE_URL` |
| [`db/copy.py`](src/fantasy_helper/db/copy.py) | `fh-copy-db`: copy every table into an empty database (local SQLite ↔ Neon) |
| [`ingest/`](src/fantasy_helper/ingest) | `fh-ingest`: fetch, validate, store, predict |
| [`context.py`](src/fantasy_helper/context.py) | Stored snapshot → analytics inputs (`LeagueContext`) |
| [`predict.py`](src/fantasy_helper/predict.py) | This week's matchup odds; `fh-predict` |
| [`waivers.py`](src/fantasy_helper/waivers.py) | Stream and hold rankings for one team; `fh-waivers` |
| [`trades.py`](src/fantasy_helper/trades.py) | Trade evaluation, the trade finder and the season outlook for one team |
| [`fit_bounce.py`](src/fantasy_helper/fit_bounce.py) | `fh-fit-bounce`: fit each category's weekly bounce from last season's ESPN game logs, for the G-score weights |
| [`api/app.py`](src/fantasy_helper/api/app.py) | FastAPI routes; `fh-api` |
| [`api/schemas.py`](src/fantasy_helper/api/schemas.py) | Response models, each from one team's side ("me" / "opp") |
| [`aws.py`](src/fantasy_helper/aws.py) | Lambda entry points |

### Analytics

The method behind each number is in the module's docstring. Start there before changing a model.

| Module | What it computes |
|---|---|
| [`zscores.py`](src/fantasy_helper/analytics/zscores.py) | Per-category player z-scores against a baseline pool (usually rostered players). FG%/FT% use volume-weighted impact, and TO is negated. A player's value is his G-score: z weighted per category by the fitted weekly bounce |
| [`rates.py`](src/fantasy_helper/analytics/rates.py) | Blended per-game rates: ESPN's projection shrunk toward season averages as games accumulate |
| [`opponent.py`](src/fantasy_helper/analytics/opponent.py) | Per-stat multipliers for the NBA opponent a player faces, shrunk toward 1 early in the season |
| [`variance.py`](src/fantasy_helper/analytics/variance.py) | Dispersion (variance / mean) per stat, fitted from box scores |
| [`discrete.py`](src/fantasy_helper/analytics/discrete.py) | Fast exact inverse CDFs, so simulations can share random numbers |
| [`lineup.py`](src/fantasy_helper/analytics/lineup.py) | Who starts on a day, given the league's starting slots (bipartite matching by priority) |
| [`matchup.py`](src/fantasy_helper/analytics/matchup.py) | Monte Carlo of the rest of a week on top of totals so far, using common random numbers |
| [`actuals.py`](src/fantasy_helper/analytics/actuals.py) | A matchup's totals so far, summed from box scores (live during games) |
| [`daily_lineup.py`](src/fantasy_helper/analytics/daily_lineup.py) | One team's week by player and day, following the lineup set on ESPN |
| [`team_profile.py`](src/fantasy_helper/analytics/team_profile.py) | Team per-game category totals, league ranks and inferred punts |
| [`waivers.py`](src/fantasy_helper/analytics/waivers.py) | Category board (locked / swing / lost) and the stream and hold rankings |
| [`trades.py`](src/fantasy_helper/analytics/trades.py) | What a deal does for both teams, and the 1-for-1 / 2-for-1 finder |
| [`power.py`](src/fantasy_helper/analytics/power.py) | Rest-of-season all-play strength and the all-play record so far |

## REST API

Every route is under `/api`. `fh-api` serves the live schema at `/docs` (Swagger UI) and `/openapi.json`.

Matchup, lineup, waiver and trade views are from one team's side (`me` / `opp`, or `me` / `partner` for trades). Where a route takes `team_id`, it defaults to `FH_MY_TEAM_ID`. `period` picks an earlier matchup week; leave it out for the current one.

| Method | Path | Query params | Returns | Used by |
|---|---|---|---|---|
| GET | `/api/health` | | `{"status": "ok"}` | Deploy smoke tests |
| GET | `/api/league` | | Season, weeks, categories, teams, last ingest | Every page (top bar) |
| GET | `/api/matchups` | `period` | Every matchup's odds for a week | Matchup |
| GET | `/api/matchup` | `team_id`, `period` | One matchup head to head: category odds, odds history | Matchup |
| GET | `/api/lineup` | `team_id`, `period` | One team's week by player and day | Matchup (`DayLineup`) |
| GET | `/api/team` | `team_id`, `window` | Roster with averages, z-scores and G-score values, category profile, recent moves | Team, Trades |
| GET | `/api/profiles` | `window` | Every team's category profile (the heatmap) | League |
| GET | `/api/power` | | Power rankings and all-play records | League |
| GET | `/api/trade` | `partner_id`, `give` (repeated), `get` (repeated), `team_id` | One deal scored for both sides | Trades |
| GET | `/api/trades` | `team_id` | Deals found that help both teams | Trades |
| GET | `/api/waivers` | `team_id`, `same_day` | Category board, stream and hold rankings | Waivers |
| POST | `/api/refresh` | | The ingest run | Refresh button |

`partner_id`, `give` and `get` are required on `/api/trade`. All other query params are optional.

- **`window`** is one of `model`, `season`, `last_7`, `last_15`, `last_30` or `projected`. The default, `model`, is the blended rate the predictions use.
- **Errors.** A `LookupError` becomes a 404 and a `ValueError` a 400, both as `{"detail": "..."}`.
- **Refresh.**
  - Returns 409 if a refresh or the scheduled ingest is already running.
  - Returns 502 if ESPN fails.
  - Within `FH_REFRESH_COOLDOWN_SECONDS` of a successful ingest, it returns that run instead of fetching again.
- **Caching.** Simulations are cached in a small in-memory LRU keyed by the snapshot (the league row's `updated_at`), so the first request after an ingest recomputes.

To add an endpoint, add a response model to `api/schemas.py` and the route to `api/app.py`, then mirror the type in [`frontend/lib/api.ts`](../frontend/lib/api.ts) (see the [frontend README](../frontend/README.md)).

## Commands

The six console scripts declared in [`pyproject.toml`](pyproject.toml); run each with `uv run`:

| Command | Flags | What it does |
|---|---|---|
| `fh-ingest` | | Fetch and store a snapshot, then store this week's predictions. Exits 1 if it fails or another ingest holds the lock |
| `fh-predict` | `--team ID`, `--sims N` | Print this week's matchup odds from the stored snapshot (doesn't store them) |
| `fh-waivers` | `--team ID`, `--today`, `--sims N`, `--top N` | Print stream and hold rankings; `--today` assumes pickups can play today |
| `fh-api` | `--host`, `--port`, `--reload` | Serve the API with uvicorn (default `127.0.0.1:8000`) |
| `fh-copy-db` | `SOURCE_URL` | Copy every table from `SOURCE_URL` into the configured database, which must be empty and at the same migration |
| `fh-fit-bounce` | `--season`, `--cache DIR`, `--pool N`, `--min-week-games N` | Fit the weekly bounce from a past season (default: last season) and write `data/weekly_bounce_<season>.json`. Run once per season; `--cache` keeps the ~560 ESPN responses for reruns |

## Configuration

`Settings` in [`config.py`](src/fantasy_helper/config.py) reads from four sources. The first one that has a value wins:

1. arguments passed in code
2. `FH_*` environment variables
3. `backend/.env` (copy [`.env.example`](.env.example))
4. the JSON secret named by `FH_SECRET_ID` (AWS Secrets Manager; needs the `aws` extra)

| Env var | Default | Notes |
|---|---|---|
| `FH_LEAGUE_ID` | `1396633554` | ESPN league |
| `FH_SEASON` | `2027` | ESPN names a season by the year it ends: 2027 is 2026-27 |
| `FH_MY_TEAM_ID` | `4` | The team views default to |
| `FH_DATABASE_URL` | SQLite at `backend/fantasy.db` | `postgres://` and `postgresql://` URLs are rewritten to use psycopg 3 |
| `FH_FREE_AGENT_POOL_SIZE` | `200` | How many free agents each ingest fetches from ESPN |
| `FH_REFRESH_COOLDOWN_SECONDS` | `0` | Set to 120 when deployed (see `.env.example`), since anyone can press Refresh |
| `FH_ESPN_S2`, `FH_SWID` | unset | Private leagues only |
| `FH_SECRET_ID` | unset | Read the settings above from this AWS secret |

## Database

The schema is in [`db/models.py`](src/fantasy_helper/db/models.py). Each ingest treats the tables one of three ways:

- **Replaced every ingest (season-scoped):** `teams`, `roster_entries`, `daily_lineups`, `player_stat_lines`, `pro_games`, `matchups`, `live_game_stats`, `live_games`.
  - `leagues` and `players` are upserted.
  - `nba_team_opponent_stats` is replaced only when its fetch succeeds.
- **History (never wiped):**
  - `player_game_stats`: daily box scores, upserted so stat corrections overwrite.
  - `moves`: adds and drops.
  - `matchup_predictions`: one row per matchup per run, append-only.
  - `ingest_runs`: one row per ingest.

Migrations are Alembic. [`alembic/env.py`](alembic/env.py) takes the URL from `Settings`, so `FH_DATABASE_URL` picks the target:

```bash
uv run alembic revision --autogenerate -m "describe the change"   # after editing models.py
uv run alembic upgrade head
uv run alembic check                                              # models and migrations agree (CI runs this on Postgres)
```

## Tests

Run them with `uv run pytest`. Lint and format with `uv run ruff check . && uv run ruff format --check .` (line length 120).

- [`tests/fakes.py`](tests/fakes.py) has minimal stand-ins for `espn_api` objects, used by the parse tests.
- [`tests/league_fixture.py`](tests/league_fixture.py) builds a synthetic, seeded 10-team league with a free-agent pool and a full NBA schedule, used by the waiver, trade, API and database tests.
- [`tests/test_postgres.py`](tests/test_postgres.py) covers what only Postgres checks: foreign keys, JSON, time zones and advisory locks. It runs only when `FH_TEST_POSTGRES_URL` names a scratch database, as it does in CI. It drops and recreates that database's tables.
