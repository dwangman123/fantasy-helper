"use client";

import { Suspense } from "react";

import { CategoryH2H } from "@/components/CategoryH2H";
import { DayLineup } from "@/components/DayLineup";
import { MatchupStrip, WeekStepper, sides, useMatchupParam, useWeekParam } from "@/components/MatchupPicker";
import { OddsBar, OddsLegend } from "@/components/OddsBar";
import { OddsHistory } from "@/components/OddsHistory";
import { ErrorNotice, Loading } from "@/components/Status";
import { Tile } from "@/components/Tile";
import { type MatchupDetail, type Team, useLeague, useMatchup, useMatchups } from "@/lib/api";
import { dateTime, pct } from "@/lib/format";

export default function MatchupPage() {
  return (
    <Suspense fallback={<Loading what="matchup" />}>
      <Matchup />
    </Suspense>
  );
}

function Matchup() {
  const league = useLeague();
  const current = league.data?.week.matchup_period;
  const [week, setWeek] = useWeekParam(current);
  // An earlier week to show; undefined while it's the current one.
  const past = week !== current ? week : undefined;
  const scoreboard = useMatchups(past, league.data !== undefined);
  const myId = league.data?.my_team_id;
  const mine = scoreboard.data?.find((r) => r.home_team_id === myId || r.away_team_id === myId);
  const [matchupId, setMatchup] = useMatchupParam(mine?.espn_id);
  const row = scoreboard.data?.find((r) => r.espn_id === matchupId) ?? mine ?? scoreboard.data?.[0];
  const [leftId, rightId] = row && myId !== undefined ? sides(row, myId) : [undefined, undefined];
  const detail = useMatchup(leftId, past);
  const error = league.error ?? scoreboard.error ?? detail.error;
  if (error) return <ErrorNotice error={error} />;
  if (!league.data || !scoreboard.data || !row || !detail.data || rightId === undefined || myId === undefined)
    return <Loading what="matchup" />;

  const d = detail.data;
  const teams = Object.fromEntries(league.data.teams.map((t) => [t.team_id, t]));
  const left = teams[d.team_id];
  const right = teams[d.opponent_id];
  const first = d.week_days[0];
  const fetching = detail.isPlaceholderData || scoreboard.isPlaceholderData;
  return (
    <div className={`stack${fetching ? " fetching" : ""}`}>
      <div className="stack" style={{ gap: 12 }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h1>Matchups</h1>
          <WeekStepper weeks={league.data.weeks} value={week ?? d.matchup_period} onChange={setWeek} />
        </div>
        <MatchupStrip
          rows={scoreboard.data}
          teams={teams}
          categories={league.data.categories.length}
          value={row.espn_id}
          myTeamId={myId}
          onChange={setMatchup}
        />
      </div>

      <section className="card">{d.final ? <Result d={d} left={left} right={right} /> : <Odds d={d} left={left} right={right} />}</section>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>Categories</h2>
            <div className="sub">
              {d.final
                ? "ESPN's final totals"
                : "Current is each total so far, including today's games. Projected is where it should end the week."}
            </div>
          </div>
          <OddsLegend labels={[`${left.owner} wins`, "Tie", `${right.owner} wins`]} />
        </div>
        <CategoryH2H rows={d.categories} left={left} right={right} final={d.final} />
      </section>

      <DayLineup key={d.espn_id} categories={league.data.categories} left={left} right={right} period={past} />

      <section className="card">
        <div className="card-head">
          <h2>{d.final ? "Win odds during the week" : "Win odds this week"}</h2>
          <span className="sub">after each refresh</span>
        </div>
        <OddsHistory points={d.history} weekStart={first} days={d.week_days.length} left={left.owner} right={right.owner} />
      </section>
    </div>
  );
}

type HeroProps = { d: MatchupDetail; left: Team; right: Team };

// The current week: each side's chance to win, the tie between them.
function Odds({ d, left, right }: HeroProps) {
  return (
    <>
      <div className="h2h-hero">
        <Side team={left} value={pct(d.p_win)} note={record(left)} color="var(--win)" />
        <div className="mid">
          <div className="sub">Chance to win the matchup · tie {pct(d.p_tie)}</div>
          <OddsBar win={d.p_win} tie={d.p_tie} loss={d.p_loss} height={14} labels={[left.owner, "Tie", right.owner]} />
        </div>
        <Side team={right} value={pct(d.p_loss)} note={record(right)} color="var(--loss)" align="right" />
      </div>
      <div className="tiles">
        <Tile
          label="Expected categories"
          value={`${d.expected.me.toFixed(1)} – ${d.expected.opp.toFixed(1)}`}
          note={`${d.expected.ties.toFixed(1)} tied`}
        />
        <Tile label="Most likely score" value={`${d.most_likely.me}-${d.most_likely.opp}-${d.most_likely.ties}`} />
      </div>
    </>
  );
}

// A finished week: categories won by each side, and what the odds said along the way.
function Result({ d, left, right }: HeroProps) {
  const { me, opp, ties } = d.expected;
  const n = me + opp + ties;
  const verdict = me > opp ? `${left.owner} won` : opp > me ? `${right.owner} won` : "Tied";
  const runs = d.history;
  return (
    <>
      <div className="h2h-hero">
        <Side team={left} value={String(me)} note="categories won" color="var(--win)" />
        <div className="mid">
          <div className="sub">
            Final · {verdict} {me}-{opp}-{ties}
          </div>
          <OddsBar win={me / n} tie={ties / n} loss={opp / n} height={14} labels={[left.owner, "Tie", right.owner]} />
        </div>
        <Side team={right} value={String(opp)} note="categories won" color="var(--loss)" align="right" />
      </div>
      {runs.length > 0 && (
        <div className="tiles">
          <Tile
            label="Odds at the first refresh"
            value={`${pct(runs[0].p_win)} – ${pct(runs[0].p_loss)}`}
            note={dateTime(runs[0].created_at)}
          />
          {runs.length > 1 && (
            <Tile
              label="Odds at the last refresh"
              value={`${pct(runs[runs.length - 1].p_win)} – ${pct(runs[runs.length - 1].p_loss)}`}
              note={dateTime(runs[runs.length - 1].created_at)}
            />
          )}
        </div>
      )}
    </>
  );
}

const record = (team: Team) => `${team.wins}-${team.losses}${team.ties ? `-${team.ties}` : ""} this season`;

type SideProps = { team: Team; value: string; note: string; color: string; align?: "right" };

function Side({ team, value, note, color, align }: SideProps) {
  return (
    <div className={`side${align === "right" ? " right" : ""}`}>
      <div className="tile-label">
        <span className="key" style={{ background: color }} />
        {team.owner}
      </div>
      <div className="hero">{value}</div>
      <div className="sub">{note}</div>
    </div>
  );
}
