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
serves matchups and waivers, and a Next.js app has Matchup and Waivers pages. A Refresh
button runs the ingest (`POST /api/refresh`).
Still open: what the league's add limit means and when a pickup can first play (the
rankings assume the next day; see PROJECT_PLAN.md).

**Week 3c (live scoring, head-to-head Matchup page, Team page): done.** A Refresh during
games is live: totals so far include today's lines, games that have tipped are locked into
their set slots, and only the rest of each live game is projected (see *Live scoring* below).
The Matchup page is picked by matchup rather than team and shows both teams head to head:
each category's totals now and projected on either side of its win / tie / loss bar, and the
lineup one day at a time (box scores, today's live lines with the game clock, projections
ahead). Its odds chart plots both teams' chance to win after each refresh. An earlier week can
be picked too: ESPN's final totals, who won each category, that week's box scores day by day,
and how the odds moved during it. A Team page shows any team's roster with per-game averages over a chosen stat window,
cells shaded by z-score and specialist tags (categories a player is 1+ SD above the rostered
average in), the team's per-game category totals with league ranks and inferred punts, and its
recent adds, drops and trades. A player's value is his G-score: his z-scores summed, each
weighted by how predictable its category is week to week (fitted from last season's weekly
game logs by `fh-fit-bounce`), so steals and the percentages count for less. The same value
orders who starts on crowded days in every simulation and which players are drop candidates.
Still to verify: that ESPN's daily rosters update box scores during a game (checked only after
games ended).

**Week 4 (trades, League page): done.** A trade is scored for both teams the way Hold scores a
pickup: the change in expected categories won per week against a league-average team over the
rest of the season, plus the change in the chance to win a week, with each side's forced roster
moves (a drop to make room, or a free agent for an open spot). The Trades page lists deals found by
searching every 1-for-1 and 2-for-1 trade with every other team and keeping those that help both
sides, and evaluates any deal of up to 3 players a side. The League page has power rankings
(rest-of-season all-play strength next to the all-play record so far) and a 10-team category
heatmap with punts marked. The measure can call a lopsided deal a gain for a team that punts the
star's categories, so each player's G-score value is shown next to him (see PROJECT_PLAN.md).

**Week 6 (AWS deploy): built, not yet applied.** Terraform in `infra/terraform/` runs the backend
as one container image on two AWS Lambda functions: the REST API behind a public function URL,
and the ingest on an hourly EventBridge Scheduler cron. The database is Postgres on Neon's free
plan, and its URL lives in Secrets Manager. The Next.js frontend is on Vercel, proxying `/api/*`
to the function URL. GitHub Actions runs the checks on every push and deploys on demand. Expected
cost is about $1 a month (see *Deploy* below and PROJECT_PLAN.md).

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

[backend/README.md](backend/README.md) covers the data flow, a guide to the modules, the REST API, the commands, the settings, the database and the tests.

## Frontend quickstart

Requires Node 20.9+. With `fh-api` running:

```bash
cd frontend
npm install
npm run dev                   # http://localhost:3000; /api/* is proxied to the backend
npm run build && npm run typecheck
```

Set `API_URL` if the backend isn't at `http://127.0.0.1:8000`.

[frontend/README.md](frontend/README.md) covers how the app calls the API, what each page shows, the URL params, the components and the conventions.

## Deploy

```
EventBridge Scheduler (hourly) -> ingest (Lambda) --> Postgres (Neon, free plan)
                                                          ^
browser -> Next.js (Vercel) -- /api/* --> API (Lambda function URL)
```

Both functions run the same image (`backend/Dockerfile`) and read their settings from one JSON
secret, `fantasy-helper/app` (`{"FH_DATABASE_URL": "..."}`), through `FH_SECRET_ID`. Reads are
public. A Refresh within 2 minutes of the last successful ingest returns that run, and a Postgres
advisory lock keeps a Refresh and the scheduled ingest from overlapping.

**Cost (estimates).** About $1 a month: the Secrets Manager secret ($0.40), image storage and
logs. Lambda stays inside its always-free tier (1M requests and 400,000 GB-seconds a month), and
Vercel Hobby is free. Neon's free plan allows 0.5 GB of storage, 100 CU-hours of compute and 5 GB
of network transfer per month. Transfer is the limit to watch late in the season, since each
context load reads the box-score history (Neon's console shows usage). Set `alert_email` to get
an AWS Budgets email at 80% of $5.

**You need:** an AWS account, a [Neon](https://neon.com) account and a [Vercel](https://vercel.com)
account; the AWS CLI with a profile (`brew install awscli`, then `aws configure sso` or
`aws configure`; credentials stay in `~/.aws`); and Terraform 1.11+ (`brew tap hashicorp/tap &&
brew install hashicorp/tap/terraform`).

### First deploy

1. **Database.** Create a Neon project in AWS us-east-1 and copy its **direct** connection string,
   not the `-pooler` host: the ingest lock needs a session of its own.
2. **Bootstrap stack:** state bucket, image repository, app secret and GitHub deploy role.
   ```bash
   cd infra/terraform/bootstrap
   terraform init && terraform apply
   ```
3. **Secret value.** In the Secrets Manager console, open `fantasy-helper/app` and store the
   plaintext `{"FH_DATABASE_URL": "postgresql://...?sslmode=require"}`. Or:
   `aws secretsmanager put-secret-value --secret-id fantasy-helper/app --secret-string '{"FH_DATABASE_URL": "..."}'`.
4. **Schema and data.** From `backend/`, before step 6 schedules any ingest: the copy needs an
   empty database. Both commands read the URL from the secret through your AWS profile (add
   `AWS_PROFILE=<name>` for a named profile). An `FH_DATABASE_URL` in `backend/.env` would take
   precedence over the secret, so make sure none is set.
   ```bash
   export FH_SECRET_ID=fantasy-helper/app AWS_REGION=us-east-1
   uv run --extra aws alembic upgrade head
   uv run --extra aws fh-copy-db sqlite:///fantasy.db
   ```
5. **First image.** In the GitHub repo's Settings > Secrets and variables > Actions > Variables, add
   `AWS_DEPLOY_ROLE_ARN` (the bootstrap's `deploy_role_arn` output) and `AWS_REGION`
   (`us-east-1`). Then run Actions > Deploy on `main`. It pushes the image and notes that the
   functions don't exist yet.
6. **Main stack:** both functions, the function URL, the schedule, logs and the budget.
   ```bash
   cd infra/terraform
   cp terraform.tfvars.example terraform.tfvars   # set alert_email
   terraform init -backend-config="bucket=<state_bucket output>" -backend-config="region=us-east-1"
   terraform apply                                # prints api_url
   ```
7. **Check that ESPN answers from AWS** with one ingest now:
   ```bash
   aws lambda invoke --function-name fantasy-helper-ingest /dev/stdout
   aws logs tail /aws/lambda/fantasy-helper-ingest --since 10m   # "ingest N succeeded"
   ```
8. **Frontend.** In Vercel, import the repo, set Root Directory to `frontend`, and add the
   environment variable `API_URL` = the `api_url` output, *before* the first build: Next.js bakes
   rewrites in at build time. Vercel then redeploys on every push to `main`.
9. **Verify.** The Vercel URL loads, and after the next full hour
   `aws logs tail /aws/lambda/fantasy-helper-ingest --since 1h` shows the scheduled run.

### After that

- **Backend code:** run Actions > Deploy. It migrates the database (`alembic upgrade head`), then
  moves both functions to the new image and checks `/api/health`. Add a `push` trigger to
  `.github/workflows/deploy.yml` to deploy on every merge instead.
- **Frontend:** Vercel deploys each push to `main`, and previews other branches.
- **Infrastructure:** `terraform plan` / `apply` in `infra/terraform`. Deploys never change the
  infrastructure, and Terraform leaves the functions' image alone.
- **Production data locally:** copy it into a fresh SQLite file:
  `FH_DATABASE_URL=sqlite:///prod.db uv run alembic upgrade head && FH_DATABASE_URL=sqlite:///prod.db uv run fh-copy-db '<neon url>'`.
- **Teardown:** `terraform destroy` in `infra/terraform`, then in `bootstrap`. The state bucket
  must be emptied first (it keeps old versions), and the secret stays recoverable for 7 days.
- **Changing the secret:** each function reads it once per cold start, so a new value applies
  after the next deploy, or once Lambda recycles its environments.

## Layout

```
backend/
  src/fantasy_helper/
    config.py        settings (FH_* env vars)
    categories.py    scoring categories, read from ESPN league settings
    calendar.py      matchup week <-> scoring period (day) mapping
    domain.py        ESPN-independent snapshot types
    espn/            ESPN fetch (client.py) and pure parsing (parse.py)
    db/              SQLAlchemy models + session, `fh-copy-db` (copy between databases)
    ingest/          snapshot persistence + `fh-ingest` job
    analytics/       pure functions: z-scores, team profiles, blended rates, lineup cap,
                     opponent factors, dispersion fit, matchup simulation, waiver rankings,
                     trade evaluation and finder, power rankings
    context.py       stored snapshot -> analytics inputs (rates, games, priorities)
    predict.py       current-week predictions from stored data + `fh-predict`
    waivers.py       stream/hold rankings for one team + `fh-waivers`
    trades.py        trade evaluation and the trade finder for one team
    fit_bounce.py    `fh-fit-bounce`: weekly bounce for the G-score weights, from last season
    api/             FastAPI app (`fh-api`)
    aws.py           Lambda entry points: the API (via Mangum) and the scheduled ingest
  alembic/           migrations
  data/              per-season matchup calendars
  tests/
  Dockerfile         the Lambda image (API by default; the ingest overrides the command)
frontend/            Next.js + TanStack Query: Matchup, Team, League, Waivers and Trades pages
infra/terraform/     bootstrap/ (state, image repo, secret, deploy role) and the main stack
.github/workflows/   ci.yml (checks on every push), deploy.yml (backend deploy, run by hand)
```

There's more on each half in [backend/README.md](backend/README.md) and [frontend/README.md](frontend/README.md).

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
- **Moves**: adds and drops come from ESPN's transactions view, re-read for the last two weeks
  (or since the latest stored move) on each ingest and kept in `moves`. Without a signed-in
  session ESPN lists trades without their players, so trades come from roster entries acquired
  by trade, with the acquisition date; a player traded away shows only on his new team.
- **History tables** are never wiped: `player_game_stats` (daily box scores; missing days are
  backfilled, and the current week's days are re-fetched for stat corrections) and `matchup_predictions`
  (one row per matchup per run, for tracking odds through the week and for backtests).
- **Opponent stats** come from ESPN's public, undocumented site API and may break without notice;
  a failed fetch keeps the last stored set and is logged as a warning.
