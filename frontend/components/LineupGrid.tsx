"use client";

import { useState } from "react";

import { InjuryBadge } from "@/components/PlayerCell";
import { ErrorNotice, Loading } from "@/components/Status";
import { type Category, type LineupCell, type Stats, type Team, type TeamLineup, useLineup } from "@/lib/api";
import { stat, weekday } from "@/lib/format";

// Percentage categories are shown as makes/attempts per game, and as a percentage in totals.
const PARTS: Record<string, [string, string]> = { "FG%": ["FGM", "FGA"], "FT%": ["FTM", "FTA"] };

// Box scores are whole numbers; projections keep a decimal.
const num = (x: number, exact: boolean) => (exact ? Math.round(x).toString() : x.toFixed(1));

function cellValue(stats: Stats, key: string, exact: boolean): string {
  const parts = PARTS[key];
  return parts ? `${num(stats[parts[0]], exact)}/${num(stats[parts[1]], exact)}` : num(stats[key], exact);
}

function totalValue(stats: Stats, key: string, exact: boolean): string {
  const parts = PARTS[key];
  if (!parts) return num(stats[key], exact);
  return stats[parts[1]] ? stat(stats[parts[0]] / stats[parts[1]], true) : "–";
}

type Props = { categories: Category[]; me: Team; opp: Team };

// Player-by-player view of the week, following the lineup set on ESPN for each day.
export function LineupGrid({ categories, me, opp }: Props) {
  const [side, setSide] = useState<"me" | "opp">("me");
  const [key, setKey] = useState("PTS");
  const team = side === "me" ? me : opp;
  const lineup = useLineup(team.team_id);

  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Lineup by day</h2>
          <div className="sub">Following the lineup set on ESPN for each day</div>
        </div>
        <div className="row">
          <div className="segmented" role="group" aria-label="Team">
            {(["me", "opp"] as const).map((s) => (
              <button key={s} type="button" aria-pressed={side === s} onClick={() => setSide(s)}>
                {(s === "me" ? me : opp).abbrev}
              </button>
            ))}
          </div>
          <label className="toggle">
            Stat
            <select value={key} onChange={(e) => setKey(e.target.value)}>
              {categories.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.key}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
      {lineup.error ? (
        <ErrorNotice error={lineup.error} />
      ) : !lineup.data ? (
        <Loading what="lineup" />
      ) : (
        <LineupTable data={lineup.data} statKey={key} fetching={lineup.isPlaceholderData} />
      )}
      <ul className="assumptions" style={{ marginTop: 12 }}>
        <li>
          <strong>Bold</strong>: box score. Regular: projected (per-game rate × opponent × chance of playing).{" "}
          <s>Struck through</s>: a game on the bench, which doesn&apos;t count.
        </li>
        <li>
          {PARTS[key] ? `${key} cells show makes/attempts. ` : ""}The win odds above assume the best lineup each day,
          so these totals can differ from the Final column.
        </li>
      </ul>
    </section>
  );
}

type TableProps = { data: TeamLineup; statKey: string; fetching: boolean };

function LineupTable({ data, statKey, fetching }: TableProps) {
  const today = data.days.findIndex((d) => !d.played);
  const allPlayed = today === -1;
  return (
    <div className={`table-wrap${fetching ? " fetching" : ""}`}>
      <table className="lineup">
        <thead>
          <tr>
            <th className="sticky">Player</th>
            {data.days.map((d, i) => (
              <th key={d.day} className="num" aria-current={i === today ? "date" : undefined}>
                {weekday(d.day)}
                {i === today && <div className="sub">today</div>}
              </th>
            ))}
            <th className="num">Week</th>
          </tr>
        </thead>
        <tbody>
          {data.players.map(({ player, cells, totals }) => (
            <tr key={player.player_id}>
              <td className="sticky">
                <span className="player-name">{player.name}</span>
                <InjuryBadge status={player.injury_status} />
                <div className="player-meta">
                  {player.pro_team} · {player.position}
                </div>
              </td>
              {cells.map((c, i) => (
                <DayCell key={data.days[i].day} cell={c} statKey={statKey} />
              ))}
              <td className="num">
                <strong>{totalValue(totals, statKey, allPlayed)}</strong>
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td className="sticky">Team total</td>
            {data.days.map((d) => (
              <td key={d.day} className="num">
                {totalValue(d.totals, statKey, d.played)}
              </td>
            ))}
            <td className="num">
              <strong>{totalValue(data.totals, statKey, allPlayed)}</strong>
            </td>
          </tr>
          <tr>
            <td className="sticky">
              Starts
              <div className="sub">games that count</div>
            </td>
            {data.days.map((d) => (
              <td key={d.day} className="num">
                {num(d.starts, d.played)}
                {!!d.missed_starts && (
                  <div>
                    <span className="chip" title="Players with a game sit on the bench while a starting slot could take them">
                      <span className="dot" style={{ background: "var(--warning)" }} />
                      {d.missed_starts} benched
                    </span>
                  </div>
                )}
              </td>
            ))}
            <td className="num">{num(data.days.reduce((n, d) => n + d.starts, 0), allPlayed)}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function DayCell({ cell, statKey }: { cell: LineupCell; statKey: string }) {
  if (!cell.slot) return <td className="num day muted">–</td>;
  if (!cell.opponent)
    return (
      <td className="num day muted">
        –<div className="meta">{cell.slot}</div>
      </td>
    );
  const kind = !cell.counts ? "bench" : cell.actual ? "played" : "projected";
  return (
    <td className={`num day ${kind}`}>
      <span className="v">{cellValue(cell.stats, statKey, cell.actual)}</span>
      <div className="meta">
        {cell.slot} · {cell.opponent}
      </div>
    </td>
  );
}
