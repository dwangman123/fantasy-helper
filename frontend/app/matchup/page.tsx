"use client";

import { Suspense } from "react";

import { CategoryH2H } from "@/components/CategoryH2H";
import { DayLineup } from "@/components/DayLineup";
import { MatchupSelect, sides, useMatchupParam } from "@/components/MatchupSelect";
import { OddsBar, OddsLegend } from "@/components/OddsBar";
import { OddsHistory } from "@/components/OddsHistory";
import { ErrorNotice, Loading } from "@/components/Status";
import { Tile } from "@/components/Tile";
import { type MatchupSummary, type Team, useLeague, useMatchup, useMatchups } from "@/lib/api";
import { pct, shortDate } from "@/lib/format";

export default function MatchupPage() {
  return (
    <Suspense fallback={<Loading what="matchup" />}>
      <Matchup />
    </Suspense>
  );
}

function Matchup() {
  const league = useLeague();
  const scoreboard = useMatchups();
  const myId = league.data?.my_team_id;
  const mine = scoreboard.data?.find((r) => r.home_team_id === myId || r.away_team_id === myId);
  const [matchupId, setMatchup] = useMatchupParam(mine?.espn_id);
  const row = scoreboard.data?.find((r) => r.espn_id === matchupId) ?? mine ?? scoreboard.data?.[0];
  const [leftId, rightId] = row && myId !== undefined ? sides(row, myId) : [undefined, undefined];
  const detail = useMatchup(leftId);
  const error = league.error ?? scoreboard.error ?? detail.error;
  if (error) return <ErrorNotice error={error} />;
  if (!league.data || !scoreboard.data || !row || !detail.data || rightId === undefined || myId === undefined)
    return <Loading what="matchup" />;

  const d = detail.data;
  const teams = Object.fromEntries(league.data.teams.map((t) => [t.team_id, t]));
  const left = teams[d.team_id];
  const right = teams[d.opponent_id];
  const first = d.week_days[0];
  const last = d.week_days[d.week_days.length - 1];
  return (
    <div className={`stack${detail.isPlaceholderData ? " fetching" : ""}`}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <h1>
            Week {d.matchup_period}: {left.name} vs {right.name}
          </h1>
          <div className="sub">
            {shortDate(first)} – {shortDate(last)} · {d.days_left.length} of {d.week_days.length} days left ·{" "}
            {d.n_sims.toLocaleString()} simulations
          </div>
        </div>
        <MatchupSelect rows={scoreboard.data} teams={teams} value={row.espn_id} myTeamId={myId} onChange={setMatchup} />
      </div>

      <section className="card">
        <div className="h2h-hero">
          <Side team={left} p={d.p_win} color="var(--win)" />
          <div className="mid">
            <div className="sub">Chance to win the matchup · tie {pct(d.p_tie)}</div>
            <OddsBar win={d.p_win} tie={d.p_tie} loss={d.p_loss} height={14} labels={[left.abbrev, "Tie", right.abbrev]} />
          </div>
          <Side team={right} p={d.p_loss} color="var(--loss)" align="right" />
        </div>
        <div className="tiles">
          <Tile
            label="Expected categories"
            value={`${d.expected.me.toFixed(1)} – ${d.expected.opp.toFixed(1)}`}
            note={`${d.expected.ties.toFixed(1)} tied`}
          />
          <Tile label="Most likely score" value={`${d.most_likely.me}-${d.most_likely.opp}-${d.most_likely.ties}`} />
          <Tile
            label="Starts left"
            value={`${d.starts_left_me.toFixed(1)} vs ${d.starts_left_opp.toFixed(1)}`}
            note="after the daily lineup cap"
          />
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>Categories</h2>
            <div className="sub">Now includes today&apos;s games so far; Final is the projected end of the week</div>
          </div>
          <OddsLegend labels={[`${left.abbrev} wins`, "Tie", `${right.abbrev} wins`]} />
        </div>
        <CategoryH2H rows={d.categories} left={left} right={right} />
      </section>

      <DayLineup key={d.espn_id} categories={league.data.categories} left={left} right={right} />

      <div className="grid-2">
        <section className="card">
          <div className="card-head">
            <h2>{left.abbrev}&apos;s win odds this week</h2>
            <span className="sub">after each refresh</span>
          </div>
          <OddsHistory points={d.history} weekStart={first} days={d.week_days.length} />
        </section>
        <section className="card">
          <div className="card-head">
            <h2>All matchups</h2>
            <OddsLegend labels={["Home", "Tie", "Away"]} />
          </div>
          <Scoreboard rows={scoreboard.data} teams={teams} selected={d.espn_id} onPick={setMatchup} />
        </section>
      </div>
    </div>
  );
}

function Side({ team, p, color, align }: { team: Team; p: number; color: string; align?: "right" }) {
  return (
    <div className={`side${align === "right" ? " right" : ""}`}>
      <div className="tile-label">
        <span className="key" style={{ background: color }} />
        {team.name}
      </div>
      <div className="hero">{pct(p)}</div>
      <div className="sub">
        {team.wins}-{team.losses}
        {team.ties ? `-${team.ties}` : ""} this season
      </div>
    </div>
  );
}

type ScoreboardProps = {
  rows: MatchupSummary[];
  teams: Record<number, Team>;
  selected: number;
  onPick: (espnId: number) => void;
};

function Scoreboard({ rows, teams, selected, onPick }: ScoreboardProps) {
  return (
    <table>
      <tbody>
        {rows.map((r) => (
          <tr
            key={r.espn_id}
            className={`clickable${r.espn_id === selected ? " selected" : ""}`}
            onClick={() => onPick(r.espn_id)}
          >
            <td>{teams[r.home_team_id]?.abbrev}</td>
            <td className="num">{pct(r.p_home)}</td>
            <td style={{ width: "45%" }}>
              <OddsBar win={r.p_home} tie={r.p_tie} loss={r.p_away} labels={["Home", "Tie", "Away"]} />
            </td>
            <td className="num">{pct(r.p_away)}</td>
            <td>{teams[r.away_team_id]?.abbrev}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
