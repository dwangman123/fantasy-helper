# Valuing a player in 9-cat H2H: options beyond summing z-scores

Research note, 2026-11-11.

How to read the labels:

- **Quotes and locators.** Each source claim carries a short quote and a locator. The three arXiv papers are CC BY 4.0. Other sources get at most one short quote each; the rest is paraphrase, marked as such.
- **Analysis** is my own reasoning.
- **Computed** is a one-off read-only run against the local DB snapshot (season 2027, taken 2026-11-09: 136 rostered players, 189 free agents, 2,530 logged player-games, my team = 4).
- **Unverified** means I could not anchor the claim.

## 1. TL;DR

- **Format.** The league is H2H most-categories (high confidence), so the true objective is P(win the week). Hold and the trade finder optimize expected categories, which Rosenof calls "not correct for Most Categories" (2307.02188v5 §4.1.4).
  - **Computed:** for my team today, the two objectives agree almost exactly on Hold (Spearman 0.999, same top 10).
- **G-score** is z with week-to-week variance added to the denominator: per category, z_k / sqrt(1 + κ·τ²/σ²).
  - It down-weights STL most (44% of its z weight in Rosenof's data), then FG%/FT%.
  - **Computed:** here it barely reorders players (Spearman 0.98-0.99 against z-sum).
- **H-scoring's in-season core is what Hold already does:** a normal approximation of category margins → P(win) → a format objective.
  - What it adds is draft machinery, a most-categories objective, and averaging over opponents.
  - The H0 paper lists three of Hold's strengths as its own gaps: the schedule, percentage volume, and team-dependent variance.
- **Replacement level.** Re-zeroing at replacement level, per category with the pool fixed, is a constant shift and can't reorder a z-sum. It reorders only if the pool or the percentage reference rate changes, or if value is multiplied by games.
- **Availability.** Per-game z is right for daily start priority and wrong for rest-of-season value. Hold assumes "everyone assumed healthy" (`analytics/waivers.py:16`).
- **Covariance** (**Computed**): bigs (FG%/REB/BLK) and guards (3PM/FT%/AST, with bad TO) are strongly correlated blocks.
  - No source quantifies how this distorts a z-sum ranking.
  - Nobody models week-to-week correlation between categories, neither H0 nor this repo.
- **SGP** is roto-only. Its H2H analogue is the P(win) slope already in `cat_slopes`.
- **Where the gains are.**
  - **Computed:** the costly z-sum use is not about z being a weak measure. Stream's drop candidates ignore this week's availability. Stream only tries dropping my 3 lowest z-sum players. Letting it consider every player found a better drop for all 25 top adds, worth +5.0 points of this-week P(win) on average (max +7.7). Every time, it was the same OUT player (Keyonte George), so this is one injury on one snapshot, not broad evidence.
  - **Checked in the DB:** this league lets OUT players sit in IR (6 teams have a plain-OUT player there), and my IR slot is empty. The free version of that move is "move him to IR, then add without dropping anyone" (§5.1).
  - **Computed:** swapping z→G, Hold's Δcats→ΔP(week), and Hold's drop list changed little or nothing.
  - **Analysis:** the next likeliest gains are in Hold's model: variance calibration (its documented saturation) and availability.

## 2. Context

Code paths below are relative to `backend/src/fantasy_helper/`. Frontend paths are relative to the repo root.

### League format: H2H most categories (high confidence)

Evidence:

- **Local DB.** A read-only query of `leagues` in the main checkout's `backend/fantasy.db` (mtime 2026-11-09) returns `2027|H2H_MOST_CATEGORIES`. That value is ESPN's `scoringSettings.scoringType` (`espn/client.py:186`).
- **`PROJECT_PLAN.md:17`.** It records the check against the live API: "scoring is `H2H_MOST_CATEGORIES` with no tiebreak (matchups can tie)".
- **`analytics/matchup.py:27`** implements the rule: "a side wins the matchup by winning more categories".
- **Fixtures.** `backend/tests/league_fixture.py:197` uses the same string, but that is a fixture, not evidence.
- **Not done:** I did not query ESPN live today.

What this changes: the manager's objective is P(win the week). Expected categories won is a proxy that Rosenof calls "a reasonable compromise" (2307.02188v5 §4.1.4).

### Where the static z-sum (`PlayerZ.total`, `analytics/zscores.py:33-35`) is still used

Z is computed on blended per-game rates, with rostered players as the baseline (`context.py:154`).

| Use | Where | Notes |
|---|---|---|
| (a) Start priority / 10-starter cap | `context.py:103` → `analytics/matchup.py:100`, `analytics/waivers.py:114` (Stream screen), `:241` (Hold/trades `day_totals`), `analytics/daily_lineup.py:158` | Also orders opponents' and the league-average teams' lineups, so it doubles as a model of other managers |
| (e) Drop candidates (not in the brief) | `waivers.py:52-57` `default_drops` (my 3 lowest z-sum); `analytics/trades.py:148` (forced drops) | Decides which drops Stream/Hold evaluate. Measured costly for Stream (§5) |
| (b) Team page | sort `api/app.py:486`; `z_total`/`specialties` `api/app.py:502-503`; `SPECIALTY_Z = 1.0` `analytics/zscores.py:91`; `frontend/app/team/page.tsx:186` (shading), `:191` | Descriptive |
| (c) Trade finder value | `api/app.py:353-354`; `frontend/app/trades/page.tsx:244`, `:372` | PROJECT_PLAN Milestone 4's "known limit" relies on it to flag lopsided deals |
| (d) Draft / general ranking | none found | No draft feature exists; n/a |

## 3. Methods

### 3.1 G-scores (Rosenof, arXiv:2307.02188v5)

**What.** "The traditional metric used by analysts, “Z-score”, … is a special case of the G-score under the condition that future player performances are known exactly" (Abstract).

**Model.** Draft "to have the highest possible expected value of number of categories won against an arbitrary opponent" (§3.1).

**Formula (Table 5c).** The total is the plain sum over categories.

- Counting: `(μ_M(q) − μ_M) / sqrt(σ_M² + κ·τ_M²)`. TO flips the numerator.
- Percentage: `(a_q/μ_A)·(μ_R(q) − μ_R) / sqrt(r_σ² + κ·τ_R²)`.
- Symbols (paraphrase of Table 5):
  - σ is the sd of player means across the pool Q.
  - τ is the root-mean-square of each player's week-to-week sd.
  - κ is the "Kappa factor", 2N/(2N−1).
  - The simulation used κ "approximated at 1" (§3.4).

**Why.**

- The category differential between two N-player teams is N(μ_M − μ_M(p), (2N−1)·σ_M² + 2N·τ_M²) (§3.3).
- "Z-scores are not as problematic for Rotisserie, because weekly variations wash out to a degree across a full season" (§4.3).

**Percentages.** "For percentage statistics, multiply expected performances by expected volume, divided by average volume" (§2).

- **Analysis:** this equals the repo's `makes − L·attempts` (`analytics/zscores.py:41`) up to the constant μ_A, when L is the same pool's composite rate. So the Z versions are identical after standardizing.
- Rosenof's website also applies a team-volume factor, N·ā/((N−1)·ā + a) (`apply_team_volume_correction`, `backend/math/process_player_data.py:559-587` at commit b7f00fb). The repo doesn't. **Analysis:** it is about 1 ± (a−ā)/(N·ā).

**Which categories it down-weights.** "Steals in particular are down-weighted heavily" (§4.3). Table 8 is 2022-23 weekly data, with Q = top 156 by Z; its "G-score as fraction of Z-score" column:

| STL | FG% | FT% | TO | PTS | BLK | REB | 3PM | AST |
|---|---|---|---|---|---|---|---|---|
| 44% | 56% | 58% | 62% | 65% | 68% | 69% | 72% | 75% |

**Results (§3.4, Table 6).**

- One G drafter against a Z field: "It won 21.4 % of simulated Each Category seasons, and 32.5 % of Most Categories seasons." Chance is 1/12.
- One Z drafter against a G field: 0.5% / 0.4%.
- Setup: twelve teams, 1,000 seasons per seat, weeks resampled from real performances, no positions (§4.1.3).

**Computed here.** For each category I multiplied the repo's z by 1/sqrt(1+r_k), with κ = 1.

| r_k = τ²/σ² from | Pool | Spearman (z-sum, G-sum) | Top-10 kept | Top-25 kept |
|---|---|---|---|---|
| Rosenof Table 8 | rostered | 0.986 | 9 | 23 |
| Rosenof Table 8 | free agents | 0.992 | 8 | 19 |
| Repo-native* | rostered | 0.981 | 9 | 22 |
| Repo-native* | free agents | 0.990 | 8 | 20 |

\*Repo-native: weekly τ² = 3.5 games × φ (`fit_dispersion`) × rate; binomial for percentages; the repo's rostered σ.

- The resulting weights are STL .54, FG% .55, FT% .64, TO .69, BLK .75, 3PM .80, PTS .82, REB .86, AST .86.
- **Analysis:** this τ² is only game-to-game noise, so it is a lower bound. It omits games-per-week swings and role or minutes drift.

Biggest rostered moves:

- Dyson Daniels (STL z +4.0) 26 → 42
- Kris Dunn 77 → 100
- James Harden 74 → 59
- Trae Young 73 → 60

**Weaknesses.**

- Static, with no team context.
- τ needs weekly history.
- **Analysis:** in season, σ² overstates the uncertainty about known rosters. H0 says this for drafted players: managers know their averages, "rendering those players’ effective player-to-player variance zero" (2409.09884v1 §3.2).
- So G-sum is a better context-free display value, not a replacement for Hold.

**Fit.** (c), and the (b) total. Little effect on (a).

### 3.2 H-scores / H0 (Rosenof, arXiv:2409.09884v1)

**What.** It argues that "static ranking systems are inherently limited because they do not allow for adaptive strategies" (§1). "The central premise of H-scoring is that the aggregate statistics of future draft picks can be approximated" from strategy parameters j: category weights and position priorities (§2-3).

**Model.**

- X(j) is a normal model of category differences, decomposed into drafted players, the candidate, future picks, and the opponent (§3.2.1).
- H0 "calculates the probability of victory for team A based on the CDF of" X(j) at zero (§3.3).
- Objectives (§3.4):
  - Each Category: Σ_c w_c.
  - Most Categories: P(winning scenario) + ½·P(tying scenario), "assuming all categories are independent".
- j is optimized per candidate with Adam (§3.5.2). It finds a local optimum (§5.2.9).

**Covariance.**

- Σ "is the covariance matrix between categories for player performance means" (§3.2.2). It is used only to forecast future picks.
- Week to week, categories are assumed independent: "This is possibly quite untrue." (§5.2.6).

**Punting emerges.**

- H0 "consistently over-performed in most of the categories, and hardly competed for the rest" (§5.1.2), in "a more subtle “soft-punting” approach" (§5.1.4).
- Its turnover weights were not reduced: "turnovers are roughly as important as other categories" (§5.1.5).

**Results (Table 3).** One H0 drafter against a G field, 2004-05 to 2023-24: "It won 21.8 % of its seasons in Each Category, and 37.7 % of its seasons in Most Categories." **Analysis:** don't compare this with G's 32.5%, which was against a Z field.

**Code and site.** The papers link no code.

- zer2.github.io links all three papers and the repo github.com/zer2/fantasy-basketball-optimizer. The repo has no license file; its last push was 2026-11-09.
- Its Season Mode has a waiver tab and a trade tab reporting H- and G-score effects (paraphrase). For waivers, H-scoring "does not iterate at all" (Season Mode page).
- Its most-categories H-score is P(winning a majority) against a random opponent, assuming independence (paraphrase, H-scoring page).

**Analysis: H0 / site vs Hold (`analytics/waivers.py:15-19`, `rank_hold`).**

| | H0 / site | Hold |
|---|---|---|
| Category P(win) | Normal CDF of the margin | Same (`weekly_cat_p_win`) |
| Objective | Each-cat sum, or P(majority) with ties ½ | Δ expected cats per week. P(week) exists only in trades (`week_odds`) |
| Opponent | Averaged over opponents (site, paraphrase) | One league-average team. `power.py` already does pairwise |
| Variance | Constant: "convenient to assume that all players contribute the same amount of variance" (§5.2.3) | φ·mean for counts, p(1−p)/attempts for percentages; scales with the team |
| Percentages | "misses the effect of volume" (§5.2.5) | Exact team makes/attempts |
| Schedule | "players don’t always have the same number of games each week" (§5.2.2, a gap) | Real schedule. The daily lineup cap is Hold's alone (neither paper discusses it) |
| Future roster | Optimizes the weight vector over future picks | One move at a time |
| Injuries | Site: υ/ψ (§3.5) | "everyone assumed healthy" |
| Trades | "does not understand the concept of trades" (§5.2.2) | Built in |
| Week-to-week correlation | Not modeled | Not modeled (`analytics/matchup.py:16`) |

### 3.3 SGP: roto only

- SGP prices the stat needed to "move up one point in the standings" (BaseballHQ, Cederholm, 2025, intro).
- The denominator is a fitted slope, "drawing a straight line between the 2nd and 14th place teams" (RotoWire, Zola, 2021).
- A secondary source credits SGP to Alex Patton, "developed by Alex Patton in the 1980s" (THT, Carty, 2008), and marginal SGP to Art McGee. **Unverified** beyond that.
- **Analysis:** the H2H analogue is d P(win cat)/d stat, which `cat_slopes` (`analytics/waivers.py:284`) and `draw_slopes` (`:120`) already compute.

### 3.4 Replacement level / VORP

**Sources.**

- **FanGraphs (Kelly, 2021).** Take the draftable pool, "mark him as a replacement-level player" for the last player, then subtract his per-category scores from everyone (paraphrase). The pool and sd stay fixed.
- **Rosenof's site code (`process_player_data.py`, b7f00fb).**
  - It re-chooses the pool as the top `n_active * n_drafters` by first-pass G (lines 89-91) and recomputes coefficients over it (93-98). So σ changes.
  - It credits missed games at replacement level, scaled per player by (1 − GP%)·ψ (lines 146, 589-611).
- **Hashtag (Muskett, 2017, MRIS).** Zero is the worst rostered player per category. This is not a z-score.

**Analysis (algebra checked).**

1. **Kelly-style re-zeroing** (pool and sd fixed) gives z′_k = z_k − c_k for every player. That is a pure shift, FG%/FT% included, so the order is unchanged.
2. **It reorders when the percentage reference rate is re-based.** Changing L in the repo's `makes − L·attempts` to a replacement-level rate shifts each player by −ΔL·attempts, which depends on volume.
3. **It reorders when the pool is re-chosen.** That changes both L and σ, which reweights categories. The repo's pool is already the rostered players.
4. **It reorders when value is multiplied by games.** The sign of (value − replacement) decides whether more games help. PROJECT_PLAN's Oct 28 finding ("187 of 190 free agents had a negative sum and more games ranked a player lower") is this effect. It also matters for position scarcity and $.
5. **In season,** replacement is the best free agent, and Hold already prices each add against its drop.

### 3.5 Availability / games played

**Sources.**

- **Rosenof's site.** It converts per-game values to expected weekly totals using games-played %, scaled by υ. "A υ of 0 is equivalent to per-game totals" (Player Stats page); the default is υ = 1. ψ (default 0.8) credits replacement-level play for missed games (paraphrase). Code: `make_upsilon_adjustment`, lines 25-41.
- **H0.** Managers "can ameliorate injury risk by swapping in un-injured players", which H0 doesn't model (§5.2.2).
- **Basketball Monster.** Separate projected per-game and total value columns (JoshG/JoshT; paraphrase, Help, "Column abbreviations").
- **Hashtag MRIS.** Applies games played after the category values (paraphrase).

**Analysis.**

- Start priority (a) is correctly per-game.
- Hold takes games from the schedule but assumes everyone is healthy.
- `p_play` (`context.py:38-43`) reflects only current status.
- The (c) display ignores both games and availability.

### 3.6 Covariance and punting

**Computed:** correlations of per-category z, rostered pool (n = 136, TO negated so higher is better).

| | FT% | 3PM | REB | AST | STL | BLK | TO | PTS |
|---|---|---|---|---|---|---|---|---|
| FG% | −.45 | −.55 | .61 | −.17 | −.08 | .42 | .11 | .02 |
| FT% | | .56 | −.43 | .23 | .14 | −.24 | −.17 | .33 |
| 3PM | | | −.52 | .24 | .10 | −.37 | −.20 | .49 |
| REB | | | | −.14 | −.10 | .53 | −.10 | .06 |
| AST | | | | | .33 | −.35 | −.71 | .49 |
| STL | | | | | | −.18 | −.19 | .21 |
| BLK | | | | | | | .14 | −.07 |
| TO | | | | | | | | −.65 |

- Var(z-sum) is 7.71, against 9.00 if the categories were independent.
- No source I found quantifies how this distorts a z-sum ranking. H0 uses Σ only to forecast picks.
- Basketball Monster's punt option removes categories from its values (paraphrase, Help, "How punting works"). Its Help also says "Values are z-scores."

**Analysis.**

- A plain sum double-counts archetypes. In H2H, what matters is the team margin, and Hold's slopes already discount punted or locked categories continuously.
- A punt-adjusted z-sum (dropping `team_profile` punts) is a cheap display tweak only.
- The real modeling gap is week-to-week correlation within a team's week, for example minutes driving PTS, FGA and TO together:
  - `analytics/matchup.py:16` draws stats independently.
  - `week_odds` assumes independence.
  - H0 has the same gap (§5.1.5, §5.2.6).

### 3.7 Commercial sites (own methods only)

- **Basketball Monster:** z-scores against your league average, per-game and total columns, category punts, and a positional value (Help). It doesn't state its pool or its percentage weighting.
- **Hashtag Basketball:** I found only the 2017 MRIS article, not a current methodology.

Neither publishes validation.

## 4. Comparison

| Method | Objective | Variance data? | Covariance? | Punts? | H2H / roto | Effort here |
|---|---|---|---|---|---|---|
| z-sum (current) | none explicit | no | no | manual only | both; better for roto | — |
| G-sum | E[cats] vs an arbitrary opponent (draft) | weekly τ | no | no | H2H | small (`zscores.py`) |
| H-score (H0) | E[cats] or P(majority), dynamic draft | τ, σ, Σ | Σ for future picks only | emerges | H2H (roto in a third paper) | large; mostly draft |
| Hold (current) | E[cats]/week vs a league average | φ | no | emerges | H2H | — |
| Stream (current) | P(win week) vs the real opponent | φ | no | emerges | H2H | — |
| SGP | roto points | standings history | no | no | roto | n/a |
| Replacement-level z | z-sum, re-zeroed | no | no | no | both | small; see §3.4 |
| Punt-adjusted z-sum | z-sum minus punts | no | no | hard-coded | both | small |

## 5. Recommendations (expected gain ÷ effort)

Four cheap checks were run for my team (team 4) on the 2026-11-09 snapshot, during week 4 on day 21:

| Check | Result |
|---|---|
| **Stream:** best drop over all 13 players vs the z-sum bottom 3, top 25 adds, same seed | **Better drop outside the bottom 3 for 25/25 adds. ΔP(win) lost by the restriction: mean 0.050, max 0.077. Top-10 overlap 8.** The best drop was always Keyonte George, listed OUT (p_play 0 this week). |
| Hold: best drop over all 13 vs the z-sum bottom 3, top 25 adds | Always inside the bottom 3 (which equals the first-order-value bottom 3); 0.000 cats/week lost |
| Hold ranked by Δcats vs ΔP(win week), all 187 free agents | Spearman 0.999; top 10 identical; 24/25 |
| z-sum vs G-sum | §3.1: Spearman 0.98-0.99 |

### 1. Suggest the IR move first, then let Stream consider drops by this week's value (measured gain)

- **Replaces:** (e) for Stream.
- **Analysis:** per-game z-sum ignores availability this week. A player who is OUT (or has no games left) costs nothing to drop for this week's matchup, yet he is never on the z-sum list.
- **IR first (checked in the DB).** OUT players are IR-eligible in this league.
  - Read-only query of `roster_entries` × `players`, 2026-11-10 snapshot: six teams (1, 2, 6, 7, 9, 10) each have a plain-`OUT` player in the `IR` slot, and `lineup_slot_counts` has `"IR": 1`.
  - Team 4 has its IR slot empty and Keyonte George `OUT` in a UT slot.
  - So the best move found above needs no drop at all: move him to IR, which opens a spot, then add.
  - `NON_ROSTER_SLOTS = {"IR"}` (`context.py`) means the sim already treats an IR-slotted player as off the roster, and `default_drops` already offers `None` when a spot is open.
- **Sketch:**
  1. When my IR slot is empty and I have an OUT player outside it, show "move X to IR" above the Stream and Hold lists, and evaluate adds as if that spot were open (drop = `None`).
  2. Only then, in `default_drops` (`waivers.py:52`), add the bottom 3 by this week's expected contribution (p_play × games left × per-game first-order value from `draw_slopes`) to the z-sum bottom 3. Or pass all roster players, which costs about 13/3 × the Stream evaluation time.
- **Required guard for real drops:** show the rest-of-season cost of the drop next to it. Hold's Δcats for the same add/drop answers that. Dropping an injured starter for a streamer can win the week and lose the season.
- **Cheap check (done above):** rerun on later snapshots. The size depends on having an injured or idle player, and the IR move covers only one such player at a time.

### 2. Calibrate Hold's category P(win), then add projection uncertainty to its variance

- **Targets:** the saturation in PROJECT_PLAN Milestone 4 ("A team far behind in a category gains almost nothing"), which also drives the trade finder.
- **Analysis:**
  - Hold's variance is game-level noise only. Over 17 remaining weeks, the rates themselves are uncertain.
  - Rosenof's τ and the site's projection over-confidence correction (H-scoring page, "Reducing over-confidence in the projection set") both widen margins, which would flatten slopes.
  - H0 saw low-end miscalibration: predicted category win rates matched "moderately well, though there was significant distortion on the lower end" (§5.1.3).
- **Sketch:** a per-category additive variance term in `weekly_cat_p_win` and `cat_slopes` (`analytics/waivers.py:260`, `:284`), fitted by backtest.
- **Cheap check:**
  - Hold's own P(win) is against a league-average team, so it can't be scored against real results.
  - What can be scored is the week-ahead per-category P(win) stored in `matchup_predictions.cat_probs` (`db/models.py:265`), against ESPN's final category results. This tests the variance model Hold shares (φ, binomial), not its multi-week horizon.
  - Too many extreme predictions that miss means widen the variance.
  - Only about 3 weeks are complete, so expect noise.

### 3. Availability in rest-of-season value

- **Replaces:** the "everyone healthy" assumption in Hold and trades.
- **Sketch:** scale each player's `weekly_stats` by an expected availability, for example season gp ÷ team games, shrunk toward a prior. Optionally credit ψ of the missed games at the free-agent replacement level, as on Rosenof's site.
- **Cheap check:** rank correlation of Hold with vs without the availability factor on the real pool. Also list the players who move more than 10 places, to see whether the moves are sensible injury-prone cases.

### 4. A games- and availability-weighted G-sum as the trade finder's context-free value

- **Replaces:** (c). The (b) total could follow.
- **Sketch:** add `gscores()` beside `player_zscores` in `analytics/zscores.py`, with weights 1/sqrt(1+r_k).
  - r_k comes from weekly box-score variance once enough weeks exist, φ-based until then, with Table 8 as the fallback.
  - Multiply by remaining games × availability.
  - Keep per-game z for shading and specialties.
- **Cheap check:** do the "star for role players" deals in PROJECT_PLAN Milestone 4 stand out more clearly by this value's gap than by the z-sum gap?

### 5. ΔP(win week) as Hold's and trades' objective (low priority)

- **Status:** measured as a near no-op for Hold on my team, which sits near 4.6 cats/week with P(week) 0.525.
- **Analysis:** it should matter more for teams far from 4.5 categories, which is likely in trade partners.
- **Sketch:**
  - Move `week_odds` into `analytics/waivers.py` (update the `trades.py` and `power.py` imports).
  - Show `d_p_week` in Hold.
  - For trades, sort on `me.d_p_week`, which is already computed.
- **Cheap check:** run the trade finder ranking by Δcats vs ΔP(week) for both sides, and report the top-10 overlap.

### 6. Context-aware start priority (measure first)

- **Replaces:** (a) for my team. Keep z or G for opponents.
- **Sketch:** priority = per-game rates · `draw_slopes`, from a second simulation pass with common random numbers kept.
- **Cheap check:** count benched-but-playing player-games per team-week in the baseline simulation. Skip this if there are few.

### Not recommended now

- Changing Hold's drop candidates (measured no gain).
- A full H-score port: its in-season core exists in Hold.
- SGP.
- Re-zeroing at replacement level for display.

## 6. Open questions and unverified items

- **Unverified:** whether the `NBA > coefficients` values in Rosenof's site `parameters.yaml` are τ²/σ² conversion factors. For example, STL is 4.64 there vs Table 8's 4.20/1.01 ≈ 4.16. I couldn't find the code that reads them.
- **Unverified:** that zer2.github.io and github.com/zer2 are Rosenof's. They link his three papers and are written in the first person, but no page names him.
- **Unverified:** SGP's origin (Patton/McGee). The only source is a secondary 2008 article. I couldn't access McGee's book.
- Not found: Hashtag Basketball's current rankings method, and Basketball Monster's pool and percentage handling.
- For 2307.02188, the arXiv listing title is "Improving Algorithms for Fantasy Basketball", but the v5 HTML is titled "Static quantification of player value for fantasy basketball". Quotes are from v5.
- Table 8's r values come from weekly 2022-23 data over a top-156 NBA pool, so the computed G-sum comparison is illustrative.
- All checks are one team and one snapshot (2026-11-09, week 4, day 21). Results may differ for other teams or later in the season. The Stream drop gain depends on having an OUT player on the roster.
- IR eligibility: resolved from league data, not league settings. Six teams hold a plain-`OUT` player in IR (§5.1). I did not read ESPN's IR-eligibility setting directly.
- **Open:** the real goal is winning the league (playoff weeks), not average weeks. H0 raises this: "Real managers may care only about winning their leagues without a preference for last place versus fourth" (§5.2.7). Neither Hold nor H0 weights playoff weeks.

## 7. Sources (accessed 2026-11-11)

- Rosenof, Z. arXiv:2307.02188v5. Listing title "Improving Algorithms for Fantasy Basketball"; v5 HTML title "Static quantification of player value for fantasy basketball". CC BY 4.0. https://arxiv.org/abs/2307.02188 · https://arxiv.org/html/2307.02188v5
- Rosenof, Z. "Dynamic quantification of player value for fantasy basketball", arXiv:2409.09884v1, CC BY 4.0. https://arxiv.org/html/2409.09884v1
- Rosenof, Z. "Optimizing for Rotisserie Fantasy Basketball", arXiv:2501.00933 (abstract only). https://arxiv.org/abs/2501.00933
- Fantasy Basketball Optimizer docs: Home, G-scores, H-scoring, Season Mode, Player Stats. https://zer2.github.io/fantasy-basketball-optimizer/
- Source code: `backend/math/process_player_data.py` at b7f00fb92ec3512a381315af920fbc6c3125efcb; `parameters.yaml` on `main`. https://github.com/zer2/fantasy-basketball-optimizer
- Basketball Monster, Help and Rankings. https://basketballmonster.com/help.aspx · https://basketballmonster.com/basketball/rankings
- Muskett, M. "How to Draft a Fantasy Basketball Team Using Analytics", Hashtag Basketball, 2017-10-15. https://hashtagbasketball.com/fantasy-basketball/content/how-to-draft-a-fantasy-basketball-team-using-analytics
- Kelly, L. "Creating Your Rankings? Start with Z-Scores", FanGraphs, 2021-12-31. https://fantasy.fangraphs.com/creating-your-rankings-start-with-z-scores/
- Cederholm, M. "Standings Gain Points for 2026", BaseballHQ, 2025-12-18 (paywalled after the intro). https://www.baseballhq.com/articles/gaming/rotisserie/standings-gain-points-for-2026
- Zola, T. "The Z Files: A Practical Approach to Standings Analysis", RotoWire, 2021-07-02. https://www.rotowire.com/baseball/article/the-z-files-a-practical-approach-to-standings-analysis-57991
- Carty, D. "My approach to player valuation", The Hardball Times, 2008-02-21 (secondary). https://tht.fangraphs.com/my-approach-to-player-valuation/
- Appelman, D. "The FanGraphs Auction Calculator!", 2015-02-23 (background only, not quoted). https://blogs.fangraphs.com/the-fangraphs-auction-calculator
- This repo (files and lines inline); local DB read-only at `/Users/dennis/projects/fantasy-helper/backend/fantasy.db`.
