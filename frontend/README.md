# Frontend

The frontend is a Next.js App Router app (React 19, TanStack Query, plain CSS). It's a thin view over the backend's REST API: all the analytics run in the backend, and the frontend only formats the results.

Install, dev server, build and the Vercel deploy are in the [root README](../README.md). The API it calls is documented in the [backend README](../backend/README.md#rest-api).

## Password

[`proxy.ts`](proxy.ts) puts the whole site, pages and `/api/*` alike, behind one shared password (HTTP Basic auth; any user name). Set `SITE_PASSWORD` to turn it on; leave it unset locally and the site is open. It doesn't cover the backend's own URL: anyone who knows the Lambda function URL can still call the API directly.

## How it talks to the backend

- **Proxy.** The browser only calls `/api/*` on its own origin. [`next.config.ts`](next.config.ts) rewrites those requests to `API_URL` (default `http://127.0.0.1:8000`). On Vercel, `API_URL` is the Terraform `api_url` output (the Lambda function URL), set before the first build (see the root README's *First deploy* steps).
- **[`lib/api.ts`](lib/api.ts)** is the only module that fetches. It has three parts:
  - The response types, which mirror [`backend/src/fantasy_helper/api/schemas.py`](../backend/src/fantasy_helper/api/schemas.py). Change both together: nothing checks that they agree.
  - A typed `get()` that builds the query string (arrays become repeated params) and throws `ApiError` with the backend's `detail` on any non-2xx response.
  - One TanStack Query hook per endpoint.
- **Query defaults** ([`app/providers.tsx`](app/providers.tsx)): a 5-minute `staleTime` and no refetch on window focus. Every hook except `useLeague` and `usePower` uses `keepPreviousData`, so switching team, week or window keeps the old view on screen (pages dim it with the `fetching` class) until the new one arrives.
- **Refresh.** `useRefresh` POSTs `/api/refresh`, which runs the ingest, and then invalidates every query, so the whole page reloads from the new snapshot.

| Hook | Endpoint |
|---|---|
| `useLeague()` | `GET /api/league` |
| `useMatchups(period?)` | `GET /api/matchups` |
| `useMatchup(teamId, period?)` | `GET /api/matchup` |
| `useLineup(teamId, period?)` | `GET /api/lineup` |
| `useTeam(teamId, window)` | `GET /api/team` |
| `useProfiles(window)` | `GET /api/profiles` |
| `usePower()` | `GET /api/power` |
| `useWaivers(teamId, sameDay)` | `GET /api/waivers` |
| `useTrade(teamId, partnerId, give, receive)` | `GET /api/trade` |
| `useTrades(teamId)` | `GET /api/trades` |
| `useRefresh()` | `POST /api/refresh` |

## Pages

The five pages are client components. [`app/layout.tsx`](app/layout.tsx) wraps them all in the query provider and the [`TopBar`](components/TopBar.tsx). The top bar has the nav links, the "data from" time and the Refresh button. When the last ingest failed or had warnings, it also shows them in a banner. `/` redirects to `/matchup`.

| Route | Shows | Hooks | Main components |
|---|---|---|---|
| [`/matchup`](app/matchup/page.tsx) | The week's matchups as cards, then any one head to head: win odds, each category's current and projected totals, the lineup day by day, odds over the week. An earlier week shows ESPN's final results | `useLeague`, `useMatchups`, `useMatchup`; `useLineup` (in `DayLineup`) | `WeekStepper`, `MatchupStrip`, `CategoryH2H`, `DayLineup`, `OddsHistory`, `OddsBar`, `Tile` |
| [`/team`](app/team/page.tsx) | A team's roster with per-game averages shaded by z-score, its category profile tinted by league rank, and recent moves | `useLeague`, `useTeam` | `TeamSelect`, `PlayerCell`, `ZShade` |
| [`/league`](app/league/page.tsx) | Power rankings and the 10-team category heatmap | `useLeague`, `usePower`, `useProfiles` | `ZShade` |
| [`/waivers`](app/waivers/page.tsx) | Category board, then free agents ranked two ways: stream (this week's odds) and hold (rest of season) | `useLeague`, `useWaivers` | `TeamSelect`, `LabelChip`, `OddsBar`, `DeltaBar`, `Effects`, `PlayerCell` |
| [`/trades`](app/trades/page.tsx) | Deals found that help both teams, and a builder to score any deal of up to 3 players a side | `useLeague`, `useTrades`, `useTeam` (both rosters), `useTrade` | `TeamSelect`, `DeltaBar`, `Effects`, `PlayerCell` |

### URL state

What's being viewed lives in the query string, so every view can be linked and reloaded. Each param is left out when it's at its default.

| Param | Pages | Hook | Default |
|---|---|---|---|
| `?team=7` | Team, Waivers, Trades | `useTeamParam` ([`TeamSelect.tsx`](components/TeamSelect.tsx)) | the first team by owner |
| `?week=2` | Matchup | `useWeekParam` ([`MatchupPicker.tsx`](components/MatchupPicker.tsx)) | the current week |
| `?matchup=12` | Matchup | `useMatchupParam` ([`MatchupPicker.tsx`](components/MatchupPicker.tsx)) | the week's first matchup; changing the week clears it |
| `?partner=7&give=1,2&get=3` | Trades | `useDeal` ([`app/trades/page.tsx`](app/trades/page.tsx)) | no deal; changing the team clears it |

The four pages that read these params (Matchup, Team, Waivers, Trades) render their view inside `<Suspense>`. Follow that pattern in a new page.

## Components

| Component | Renders |
|---|---|
| [`TopBar`](components/TopBar.tsx) | Nav, data time, Refresh button and the ingest-warnings banner |
| [`RefreshButton`](components/RefreshButton.tsx) | Runs `useRefresh` and shows its error |
| [`TeamSelect`](components/TeamSelect.tsx) | Team picker, plus `useTeamParam` |
| [`MatchupPicker`](components/MatchupPicker.tsx) | `WeekStepper` (back and forward through the weeks) and `MatchupStrip` (a card per matchup with each side's win odds, or categories won once the week is over), `useWeekParam` / `useMatchupParam`, and `sides()`, which puts my team on the left (otherwise home) |
| [`CategoryH2H`](components/CategoryH2H.tsx) | Each category head to head: current and projected totals on either side of its win / tie / loss bar, with the locked / swing status centered under it |
| [`DayLineup`](components/DayLineup.tsx) | One day of the matchup: both teams' players in their ESPN slots, each game as one fantasy value (z-scores summed on the single-game scale) shaded against a typical game, with the player's usual and the two categories that moved it |
| [`OddsHistory`](components/OddsHistory.tsx) | Both teams' win odds after each ingest, across the week (SVG) |
| [`OddsBar`](components/OddsBar.tsx) | Win / tie / loss as one 100% bar, plus `OddsLegend` |
| [`DeltaBar`](components/DeltaBar.tsx) | A signed change as a bar from a center line: blue right when it helps, red left when it hurts |
| [`Effects`](components/Effects.tsx) | Chips for the categories a move changes most; categories that flip lead and are bold |
| [`LabelChip`](components/LabelChip.tsx) | A locked / swing / lost category label |
| [`ZShade`](components/ZShade.tsx) | `zClass(z)` for z-score cell shading, plus `ZLegend` |
| [`PlayerCell`](components/PlayerCell.tsx) | Player name, team, position and injury badge |
| [`Tile`](components/Tile.tsx) | A labeled headline number |
| [`Status`](components/Status.tsx) | `Loading` and `ErrorNotice`. The notice suggests `fh-ingest` on a 404 and `fh-api` on any other error |

Formatting helpers are in [`lib/format.ts`](lib/format.ts): percentages, signed changes, category totals, dates, and the stat-window labels.

## Conventions

- **One team's side.** Matchup, waiver and trade responses are from one team's point of view (`me` / `opp`, `me` / `partner`), so the UI never works out home or away. Blue (`--win`) is the viewed team's side and red (`--loss`) the other's. On the Matchup page the left team is the home team (`sides()` in `MatchupPicker.tsx`), and each matchup card puts a key square on each name to match.
- **No viewer identity.** Anyone in the league may be looking, so the UI never marks a team as "mine" and doesn't read `my_team_id`; views default to the first team or matchup.
- **Teams go by their owner.** Show `team.owner` (the owner's first name, from the backend's `owner_names()`), never the ESPN team name or abbreviation. A snapshot stored before owners were kept sends the team name there until the next ingest.
- **Percentages come from makes and attempts.** FG% and FT% are computed from summed FGM/FGA and FTM/FTA, never averaged (`PARTS`, `totalValue` in `lib/format.ts`).
- **Dates.** The API sends days as `YYYY-MM-DD`. Parse them as local days with the helpers in `lib/format.ts`, not with `new Date()`, which would read them as UTC midnight.
- **Z-shading.** Two shades of blue from +0.5 and +1 SD, two of red from −0.5 and −1 SD, unshaded within half an SD of average ([`ZShade.tsx`](components/ZShade.tsx)).
- **Colors and theme.** Every color is a CSS variable in [`app/globals.css`](app/globals.css), with a dark-mode override under `prefers-color-scheme: dark`. Use the variables rather than literal colors.
- **Stat windows.** The Team and League pages offer `model`, `season`, `last_7`, `last_15`, `last_30` and `projected` (`WINDOW_LABELS`). `model` is the blended rate the predictions use.

## Adding a page

1. Add the route and its response model in the backend (`api/app.py`, `api/schemas.py`).
2. Mirror the response type in `lib/api.ts` and add a hook for it.
3. Create `app/<name>/page.tsx` as a client component. If it reads URL params, wrap the view in `<Suspense>`. Use `Loading` and `ErrorNotice` for the empty and error states.
4. Add the link to `LINKS` in `components/TopBar.tsx`.
5. Check it with `npm run typecheck && npm run build`, as CI does.
