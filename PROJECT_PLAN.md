# Fantasy Helper — NBA 9-cat H2H App Plan

## Context
Greenfield project (`/Users/dennis/Documents/projects/fantasy-helper` is empty). Two goals:
1. **Resume value**: a deployed, well-engineered full-stack app with a data core and an LLM agent on top. This reads well for backend, data, and AI roles.
2. **Win the league**: a 10-team, 9-category head-to-head ESPN league (public, single league, just me). Help with waivers, trades, matchup scouting, and league/punt analysis.

Decisions made: Python (FastAPI) + React (Next.js), Postgres, AWS, OpenAI tool-calling agent, ESPN data (nba_api optional), 1–2 month timeline.

Verified on day 1 (2026-10-20): league "Bicoastal Ballaz", ID 1396633554, season 2027, my team ID 4. Categories are the standard ESPN 9 (FG%, FT%, 3PM, REB, AST, STL, BLK, TO reversed, PTS), read from league settings. 19 regular-season weeks, then a 4-team playoff with two 2-week rounds.

### Findings that changed the plan
- **The schedule comes from ESPN.** Player data includes the full NBA schedule by scoring period (day), so games-remaining needs no second source. `nba_api` is now optional (only for game logs, if ever needed), which also removes the stats.nba.com-blocks-AWS risk.
- **Matchup weeks are irregular, and the API doesn't expose them.** Week 1 is 6 days and week 18 is 14 days (All-Star break). The calendar is a checked-in data file, validated on every ingest.
- **Local dev uses SQLite** (Docker isn't installed yet). The schema is portable; `docker-compose.yml` is ready for Postgres.
- **The project lives in iCloud-synced ~/Documents**, which hides the venv's `.pth` files and breaks imports. Tests and Alembic are configured with `src` on the path; moving the project out of iCloud is the real fix.
- **Live matchup predictor (added 2026-10-23).** Facts checked against the live API: scoring is `H2H_MOST_CATEGORIES` with no tiebreak (matchups can tie); 10 starting slots (PG, SG, SF, PF, C, G, F, UT×3) + 3 BE + 1 IR, locked per game, so benched games don't count; `mMatchup` scores include FGM/FGA/FTM/FTA, so live percentages combine with projected makes/attempts; past days' box scores come from `mRoster`/`kona_player_info` with `scoringPeriodId`; ESPN has no per-day projections, so we build them.
- **Predictions update day by day, not during games.** The ingest should run once a day before the first tip-off (weekend games can start around 9am PT). A run after tip-off double-counts today's games (they're in ESPN's live totals and also projected); it's not corrected, only flagged in `ingest_runs.warnings`.
- **Opponent stats come from ESPN's public, undocumented site API** (`statistics/byteam?category=opponent`), which can change without notice. If the fetch fails, the last stored set is kept and predictions fall back to no adjustment. Its team codes differ from the fantasy API's for 8 teams (mapped in `espn/parse.py`).

## Architecture
```
EventBridge (cron) → ingest job (ECS task) → Postgres (RDS)
                                               ↑
Next.js frontend ←→ FastAPI (App Runner/ECS) ──┤
                       └─ /agent → OpenAI tool-calling loop → calls the same analytics services
```
- **Ingestion** (`backend/src/fantasy_helper/ingest/`): `espn-api` provides league settings (incl. lineup slots and scoring type), rosters, free agents, stat windows, the NBA schedule and matchups (category totals plus makes/attempts). Replaces season tables atomically. Also keeps history that is never wiped: daily box scores (`player_game_stats`, missing days backfilled) and one prediction row per matchup per run (`matchup_predictions`, append-only). Opponent stats (`nba_team_opponent_stats`) come from ESPN's public site API. Runs **once a day, before the first tip-off**. ✅ built
- **Analytics core** (`backend/src/fantasy_helper/analytics/`): pure, unit-tested functions. This is the heart of the project:
  - `zscores.py`: per-cat z-scores over a configurable window (season / L14 / L7). FG% and FT% are **volume-weighted impact** (makes − league% × attempts). TO is negated.
  - `team_profile.py`: team per-cat totals, ranks, inferred punt cats.
  - `matchup.py` ✅: live weekly prediction = actual totals so far + Monte Carlo of the days left. Per player-game: blended rate (`rates.py`) × opponent factor (`opponent.py`), a chance of playing from injury status, and the daily 10-starter cap (`lineup.py`, priority by z-score). Variance is Poisson until box-score history allows a fitted dispersion (`variance.py`). Output: P(win/tie/loss), P(win) per cat, expected and most likely category score, projected finals. Run by the ingest and by `fh-predict`.
  - `waivers.py`: rank free agents by Δ in *my* matchup/season cat win-probabilities when swapped for my worst roster player, not by generic rank.
  - `trades.py`: evaluate a trade (Δ cat win odds for both sides). Trade finder searches 1-for-1 and 2-for-1 across 9 teams for mutually positive deals.
- **API** (`backend/src/fantasy_helper/api/`): REST endpoints wrapping each analytics function.
- **Agent** (`backend/src/fantasy_helper/agent/`): OpenAI function calling. Tools = `get_my_team`, `get_matchup_projection`, `rank_waivers`, `evaluate_trade`, `find_trades`, `get_team_profile`, `player_lookup`. The LLM only explains and orchestrates; all numbers come from deterministic tools. Add a small **eval suite** (`agent/evals/`) of questions with expected tool calls and answer checks. This is a strong resume bullet.
- **Frontend** (`frontend/`, Next.js + TanStack Query + a chart lib): pages for Dashboard, Matchup, Waivers, Trade Lab, League Heatmap, Chat.

## Milestones (~8 weeks)
1. **Wk 1 – Data foundation** ✅: uv project, SQLAlchemy + Alembic, ESPN ingest, schema (leagues, teams, players, roster_entries, player_stat_lines, pro_games, matchups, ingest_runs), matchup calendar, 13 tests.
2. **Wk 2 – Analytics core** ✅: z-scores, team profiles, tests with pytest and fixture data.
3. **Wk 3 – Matchup + Waivers (MVP)**
   - **3a – Live matchup predictor** ✅: daily H2H win odds and expected category score. Required ingest additions (makes/attempts, lineup slots, box-score history, opponent stats) and new analytics (blended rates, lineup cap, opponent factors, dispersion fit).
   - **3b – Waivers + API + basic UI**: waiver ranking reuses `matchup.py` (Δ win odds from a swap); REST endpoints and a first UI, including the matchup view. *Usable for the season from here.*
4. **Wk 4 – Trades + League view**: trade evaluator/finder, 10-team category heatmap, punt detection, power rankings.
5. **Wk 5 – Agent**: OpenAI tool loop, streaming chat UI, evals.
6. **Wk 6 – AWS deploy**: Terraform (RDS, ECS/App Runner, EventBridge, Secrets Manager for the OpenAI key), GitHub Actions CI/CD, frontend on Amplify/Vercel.
7. **Wk 7–8 – Polish + resume extras**: backtest the stored `matchup_predictions` against final weekly results (Brier score / calibration; also tunes the blend constant, opponent shrinkage and day-to-day P(play)), observability (structured logs, agent token/cost tracking), README with architecture diagram, short demo video.

## Repo layout
```
fantasy-helper/
  backend/src/fantasy_helper/{espn,db,ingest,analytics,api,agent}/  backend/tests/
  frontend/          infra/terraform/   .github/workflows/   docker-compose.yml
```

## Verification
- `pytest` on analytics with hand-computed fixtures (z-scores, %-cat weighting, sim sanity: identical teams → ~50%).
- Ingest run against the real league ID; spot-check rosters vs. the ESPN site.
- Backtest: matchup sim vs. completed weeks this season.
- Agent evals pass; manual chat check: "who should I pick up to win STL this week?"
- Deployed URL loads, and the scheduled ingest updates data (check CloudWatch logs).
