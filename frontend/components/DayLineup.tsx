"use client";

import { useState } from "react";

import { InjuryBadge } from "@/components/PlayerCell";
import { ErrorNotice, Loading } from "@/components/Status";
import { ZLegend, zClass } from "@/components/ZShade";
import { type LineupCell, type Player, type Stats, type Team, type TeamLineup, useLineup } from "@/lib/api";
import { signed, timeOfDay, weekday } from "@/lib/format";

// ESPN's lineup order: starters first, then bench and IR.
const SLOT_ORDER = ["PG", "SG", "SF", "PF", "C", "G", "F", "UT", "BE", "IR"];
const slotRank = (slot: string | null) => (slot && SLOT_ORDER.includes(slot) ? SLOT_ORDER.indexOf(slot) : SLOT_ORDER.length);

type Scale = TeamLineup["value_scale"];

// A value to one decimal, signed, without a "−0.0".
const val = (x: number) => signed(Math.round(x * 10) / 10 || 0, 1);

// `period` is an earlier week to show; undefined is the current one.
type Props = { left: Team; right: Team; period?: number };

// One day of the matchup at a time: each team's players with a game that day, in the lineup
// set on ESPN, each game as one fantasy value (box score for days played, live line today,
// projection ahead), shaded against a typical game.
export function DayLineup({ left, right, period }: Props) {
  const leftLineup = useLineup(left.team_id, period);
  const rightLineup = useLineup(right.team_id, period);
  const [picked, setPicked] = useState<number | null>(null);
  const days = leftLineup.data?.days ?? rightLineup.data?.days ?? [];
  const today = days.findIndex((d) => !d.played);
  const day = picked ?? (today === -1 ? days.length - 1 : today);
  const error = leftLineup.error ?? rightLineup.error;
  const fetching = leftLineup.isPlaceholderData || rightLineup.isPlaceholderData;

  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Lineup by day</h2>
          <div className="sub">Each game as one fantasy value, in the lineup set on ESPN</div>
        </div>
        <ZLegend />
      </div>
      {days.length > 0 && (
        <div className="segmented day-tabs" role="group" aria-label="Day">
          {days.map((d, i) => (
            <button
              key={d.day}
              type="button"
              aria-pressed={i === day}
              aria-current={i === today ? "date" : undefined}
              onClick={() => setPicked(i)}
            >
              {weekday(d.day)}
              {i === today && <span className="today-mark"> · today</span>}
            </button>
          ))}
        </div>
      )}
      {error ? (
        <ErrorNotice error={error} />
      ) : !leftLineup.data || !rightLineup.data ? (
        <Loading what="lineups" />
      ) : (
        <div className={`day-h2h${fetching ? " fetching" : ""}`}>
          <DaySide team={left} data={leftLineup.data} day={day} isToday={day === today} side="left" />
          <DaySide team={right} data={rightLineup.data} day={day} isToday={day === today} side="right" />
        </div>
      )}
      <ul className="assumptions" style={{ marginTop: 16 }}>
        <li>
          A game&apos;s value adds up its z-scores, each against how much single games by rostered players vary this
          season, so an empty category costs about one point, not several. FG% and FT% count by volume, and fewer
          turnovers is better. &quot;usual&quot; is his per-game averages scored the same way: what his games average
          out to.
        </li>
        <li>
          Shading compares the game with a typical rostered game. The chips are the two categories that moved it most.
          {period === undefined
            ? " Bold: box score (today: the line so far). Regular: projected (per-game rate × opponent × chance of playing)."
            : " Box scores are the ones stored when each day was fetched, so ESPN's later stat corrections can leave one a stat off."}{" "}
          <s>Struck through</s>: a game on the bench, which doesn&apos;t count.
        </li>
      </ul>
    </section>
  );
}

type SideProps = { team: Team; data: TeamLineup; day: number; isToday: boolean; side: "left" | "right" };

function DaySide({ team, data, day, isToday, side }: SideProps) {
  const d = data.days[day];
  // In that day's slot order; a stable sort keeps the API's order (priority) within a slot.
  const playing = data.players
    .filter((p) => p.cells[day]?.slot && p.cells[day].opponent)
    .sort((a, b) => slotRank(a.cells[day].slot) - slotRank(b.cells[day].slot));
  const idle = data.players.filter((p) => p.cells[day]?.slot && !p.cells[day].opponent && p.cells[day].slot !== "IR");
  // Today is settled once nothing counted is left to project, e.g. every game is final.
  const settled = Math.abs(d.projected_value - d.actual_value) < 1e-9;
  const total =
    d.played || (isToday && settled)
      ? `Final ${val(d.actual_value)}`
      : isToday
        ? `Live ${val(d.actual_value)} · proj final ${val(d.projected_value)}`
        : `Proj ${val(d.projected_value)}`;
  return (
    <div className={`day-side ${side}`}>
      <div className="day-side-head">
        <span className="key" style={{ background: side === "left" ? "var(--win)" : "var(--loss)" }} />
        <strong>{team.owner}</strong>
      </div>
      {playing.length === 0 ? (
        <div className="sub">No games this day.</div>
      ) : (
        <table className="day-table">
          <tbody>
            {playing.map(({ player, cells, usual }) => (
              <DayRow
                key={player.player_id}
                player={player}
                cell={cells[day]}
                usual={usual}
                scale={data.value_scale}
                side={side}
              />
            ))}
          </tbody>
        </table>
      )}
      <div className="day-total">
        <strong>{total}</strong>
        <span className="sub">
          {d.played ? Math.round(d.starts) : d.starts.toFixed(1)} starts
          {!!d.missed_starts && (
            <span
              className="chip"
              style={{ marginLeft: 8 }}
              title="Players with a game sit on the bench while a starting slot could take them"
            >
              <span className="dot" style={{ background: "var(--warning)" }} />
              {d.missed_starts} benched
            </span>
          )}
        </span>
      </div>
      {idle.length > 0 && <div className="sub idle">No game: {idle.map((p) => p.player.name).join(", ")}</div>}
    </div>
  );
}

type RowProps = {
  player: Player;
  cell: LineupCell;
  usual: number | null;
  scale: Scale;
  side: "left" | "right";
};

function DayRow({ player, cell, usual, scale, side }: RowProps) {
  const kind = !cell.counts ? "bench" : cell.actual ? "played" : "projected";
  const live = cell.game?.state === "live";
  const value = cell.value ?? 0;
  const shade = cell.counts && scale.sd > 0 ? zClass((value - scale.mean) / scale.sd) : "";
  const cells = [
    <td key="slot" className="slot">
      {cell.slot}
    </td>,
    <td key="player">
      <span className="player-name">{player.name}</span>
      <InjuryBadge status={player.injury_status} />
      <div className="player-meta">
        vs {cell.opponent}
        <GameStatus cell={cell} />
      </div>
      {cell.actual && <div className="meta">{boxLine(cell.stats)}</div>}
      {cell.actual && cell.value_by_cat && <Drivers byCat={cell.value_by_cat} />}
    </td>,
    <td key="value" className={`num value zcell ${kind} ${shade}`}>
      <span className="v">{val(value)}</span>
      {live && cell.projected_value !== null && <div className="meta">proj {val(cell.projected_value)}</div>}
      {!cell.actual && <div className="meta">proj</div>}
      {usual !== null && <div className="meta">usual {val(usual)}</div>}
    </td>,
  ];
  return <tr>{side === "left" ? cells : cells.reverse()}</tr>;
}

// The box score in brief: points, rebounds and assists, the rarer counting stats when there
// are at least two, and shooting.
function boxLine(s: Stats): string {
  const parts = [`${s.PTS} pts`, `${s.REB} reb`, `${s.AST} ast`];
  for (const [key, label] of [
    ["STL", "stl"],
    ["BLK", "blk"],
    ["3PM", "3pm"],
  ]) {
    if (s[key] >= 2) parts.push(`${s[key]} ${label}`);
  }
  parts.push(`${s.FGM}/${s.FGA} fg`);
  return parts.join(" · ");
}

// The two categories that moved the game's value most.
function Drivers({ byCat }: { byCat: Record<string, number> }) {
  const top = Object.keys(byCat)
    .sort((a, b) => Math.abs(byCat[b]) - Math.abs(byCat[a]))
    .slice(0, 2);
  return (
    <div className="effects drivers">
      {top.map((k) => (
        <span key={k} className="chip">
          <span className="dot" style={{ background: byCat[k] >= 0 ? "var(--win)" : "var(--loss)" }} />
          {k} {val(byCat[k])}
        </span>
      ))}
    </div>
  );
}

function GameStatus({ cell }: { cell: LineupCell }) {
  const game = cell.game;
  if (!game) return null;
  if (game.state === "pre") return game.tip_at ? <> · {timeOfDay(game.tip_at)}</> : null;
  return (
    <>
      {" · "}
      <span className={game.state === "live" ? "live" : undefined}>{game.status}</span>
    </>
  );
}
