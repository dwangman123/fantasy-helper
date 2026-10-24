"use client";

import { Suspense } from "react";

import { LabelChip } from "@/components/LabelChip";
import { OddsBar, OddsLegend } from "@/components/OddsBar";
import { OddsHistory } from "@/components/OddsHistory";
import { ErrorNotice, Loading } from "@/components/Status";
import { TeamSelect, useTeamParam } from "@/components/TeamSelect";
import { Tile } from "@/components/Tile";
import { type CategoryOdds, type MatchupSummary, type Team, useLeague, useMatchup, useMatchups } from "@/lib/api";
import { pct, shortDate, stat } from "@/lib/format";

export default function MatchupPage() {
  return (
    <Suspense fallback={<Loading what="matchup" />}>
      <Matchup />
    </Suspense>
  );
}

function Matchup() {
  const league = useLeague();
  const [teamId, setTeam] = useTeamParam(league.data?.my_team_id);
  const detail = useMatchup(teamId);
  const scoreboard = useMatchups();
  const error = league.error ?? detail.error;
  if (error) return <ErrorNotice error={error} />;
  if (!league.data || !detail.data || teamId === undefined) return <Loading what="matchup" />;

  const d = detail.data;
  const teams = Object.fromEntries(league.data.teams.map((t) => [t.team_id, t]));
  const me = teams[d.team_id];
  const opp = teams[d.opponent_id];
  const first = d.week_days[0];
  const last = d.week_days[d.week_days.length - 1];
  return (
    <div className={`stack${detail.isPlaceholderData ? " fetching" : ""}`}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <h1>
            Week {d.matchup_period}: {me.name} vs {opp.name}
          </h1>
          <div className="sub">
            {shortDate(first)} – {shortDate(last)} · {d.days_left.length} of {d.week_days.length} days left ·{" "}
            {d.n_sims.toLocaleString()} simulations
          </div>
        </div>
        <TeamSelect teams={league.data.teams} value={teamId} myTeamId={league.data.my_team_id} onChange={setTeam} />
      </div>

      <section className="card">
        <div className="tile-label">Chance to win the matchup</div>
        <div className="hero">{pct(d.p_win)}</div>
        <div className="sub" style={{ marginTop: 6 }}>
          Tie {pct(d.p_tie)} · Loss {pct(d.p_loss)}
        </div>
        <div style={{ marginTop: 16 }}>
          <OddsBar win={d.p_win} tie={d.p_tie} loss={d.p_loss} height={14} />
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
          <h2>Categories</h2>
          <OddsLegend />
        </div>
        <CategoryTable rows={d.categories} me={me} opp={opp} />
      </section>

      <div className="grid-2">
        <section className="card">
          <div className="card-head">
            <h2>Win odds this week</h2>
            <span className="sub">after each daily ingest</span>
          </div>
          <OddsHistory points={d.history} weekStart={first} days={d.week_days.length} />
        </section>
        <section className="card">
          <div className="card-head">
            <h2>All matchups</h2>
            <OddsLegend labels={["Home", "Tie", "Away"]} />
          </div>
          {scoreboard.data ? (
            <Scoreboard rows={scoreboard.data} teams={teams} selected={d.espn_id} onPick={setTeam} />
          ) : (
            <Loading what="matchups" />
          )}
        </section>
      </div>
    </div>
  );
}

function CategoryTable({ rows, me, opp }: { rows: CategoryOdds[]; me: Team; opp: Team }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Category</th>
            <th className="num hide-sm">Now {me.abbrev}</th>
            <th className="num hide-sm">Now {opp.abbrev}</th>
            <th className="num hide-sm">Final {me.abbrev}</th>
            <th className="num hide-sm">Final {opp.abbrev}</th>
            <th style={{ width: "28%" }}>Win · tie · loss</th>
            <th className="num">P(win)</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.key}>
              <td>
                <strong>{c.key}</strong>
                {c.reverse && <div className="sub">fewer wins</div>}
              </td>
              <td className="num hide-sm">{stat(c.now_me, c.is_percentage)}</td>
              <td className="num hide-sm">{stat(c.now_opp, c.is_percentage)}</td>
              <td className="num hide-sm">{stat(c.proj_me, c.is_percentage)}</td>
              <td className="num hide-sm">{stat(c.proj_opp, c.is_percentage)}</td>
              <td>
                <OddsBar win={c.p_win} tie={c.p_tie} loss={c.p_loss} />
              </td>
              <td className="num">{pct(c.p_win)}</td>
              <td>
                <LabelChip label={c.label} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type ScoreboardProps = {
  rows: MatchupSummary[];
  teams: Record<number, Team>;
  selected: number;
  onPick: (teamId: number) => void;
};

function Scoreboard({ rows, teams, selected, onPick }: ScoreboardProps) {
  return (
    <table>
      <tbody>
        {rows.map((r) => (
          <tr
            key={r.espn_id}
            className={`clickable${r.espn_id === selected ? " selected" : ""}`}
            onClick={() => onPick(r.home_team_id)}
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
