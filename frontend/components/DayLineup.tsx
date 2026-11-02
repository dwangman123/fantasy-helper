"use client";

import { useState } from "react";

import { InjuryBadge } from "@/components/PlayerCell";
import { ErrorNotice, Loading } from "@/components/Status";
import { type Category, type LineupCell, type Player, type Team, type TeamLineup, useLineup } from "@/lib/api";
import { PARTS, lineValue, num, timeOfDay, totalValue, weekday } from "@/lib/format";

// ESPN's lineup order: starters first, then bench and IR.
const SLOT_ORDER = ["PG", "SG", "SF", "PF", "C", "G", "F", "UT", "BE", "IR"];
const slotRank = (slot: string | null) => (slot && SLOT_ORDER.includes(slot) ? SLOT_ORDER.indexOf(slot) : SLOT_ORDER.length);

// `period` is an earlier week to show; undefined is the current one.
type Props = { categories: Category[]; left: Team; right: Team; period?: number };

// One day of the matchup at a time: each team's players with a game that day, in the lineup
// set on ESPN, with box scores for days played, live lines today and projections ahead.
export function DayLineup({ categories, left, right, period }: Props) {
  const leftLineup = useLineup(left.team_id, period);
  const rightLineup = useLineup(right.team_id, period);
  const [picked, setPicked] = useState<number | null>(null);
  const [key, setKey] = useState("PTS");
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
          <div className="sub">Players with a game, in the lineup set on ESPN</div>
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
          <DaySide team={left} data={leftLineup.data} day={day} isToday={day === today} statKey={key} side="left" />
          <DaySide team={right} data={rightLineup.data} day={day} isToday={day === today} statKey={key} side="right" />
        </div>
      )}
      <ul className="assumptions" style={{ marginTop: 16 }}>
        {period === undefined ? (
          <>
            <li>
              <strong>Bold</strong>: box score (today: the line so far). Regular: projected (per-game rate × opponent ×
              chance of playing). <s>Struck through</s>: a game on the bench, which doesn&apos;t count.
            </li>
            <li>
              {PARTS[key] ? `${key} shows makes/attempts. ` : ""}The win odds above assume the best lineup from here on,
              so these totals can differ from the Final column.
            </li>
          </>
        ) : (
          <>
            <li>
              Box scores in the slot each player held that day. <s>Struck through</s>: a game on the bench, which
              didn&apos;t count. {PARTS[key] ? `${key} shows makes/attempts.` : ""}
            </li>
            <li>
              These are the box scores stored when each day was fetched, so ESPN&apos;s later stat corrections can leave
              a day&apos;s total a stat or two off. The categories above are ESPN&apos;s final totals.
            </li>
          </>
        )}
      </ul>
    </section>
  );
}

type SideProps = {
  team: Team;
  data: TeamLineup;
  day: number;
  isToday: boolean;
  statKey: string;
  side: "left" | "right";
};

function DaySide({ team, data, day, isToday, statKey, side }: SideProps) {
  const d = data.days[day];
  // In that day's slot order; a stable sort keeps the API's order (priority) within a slot.
  const playing = data.players
    .filter((p) => p.cells[day]?.slot && p.cells[day].opponent)
    .sort((a, b) => slotRank(a.cells[day].slot) - slotRank(b.cells[day].slot));
  const idle = data.players.filter((p) => p.cells[day]?.slot && !p.cells[day].opponent && p.cells[day].slot !== "IR");
  // Today is settled once nothing counted is left to project, e.g. every game is final.
  const settled = (PARTS[statKey] ?? [statKey]).every((k) => Math.abs(d.projected[k] - d.actual[k]) < 1e-9);
  const total =
    d.played || (isToday && settled)
      ? `Final ${totalValue(d.actual, statKey, true)}`
      : isToday
        ? `Live ${totalValue(d.actual, statKey, true)} · proj final ${totalValue(d.projected, statKey, false)}`
        : `Proj ${totalValue(d.projected, statKey, false)}`;
  return (
    <div className={`day-side ${side}`}>
      <div className="day-side-head">
        <span className="key" style={{ background: side === "left" ? "var(--win)" : "var(--loss)" }} />
        <strong>{team.name}</strong>
      </div>
      {playing.length === 0 ? (
        <div className="sub">No games this day.</div>
      ) : (
        <table className="day-table">
          <tbody>
            {playing.map(({ player, cells }) => (
              <DayRow key={player.player_id} player={player} cell={cells[day]} statKey={statKey} side={side} />
            ))}
          </tbody>
        </table>
      )}
      <div className="day-total">
        <strong>{total}</strong>
        <span className="sub">
          {num(d.starts, d.played)} starts
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

type RowProps = { player: Player; cell: LineupCell; statKey: string; side: "left" | "right" };

function DayRow({ player, cell, statKey, side }: RowProps) {
  const kind = !cell.counts ? "bench" : cell.actual ? "played" : "projected";
  const live = cell.game?.state === "live";
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
    </td>,
    <td key="value" className={`num value ${kind}`}>
      <span className="v">{lineValue(cell.stats, statKey, cell.actual)}</span>
      {live && <div className="meta">proj {lineValue(cell.projected, statKey, false)}</div>}
      {!cell.actual && <div className="meta">proj</div>}
    </td>,
  ];
  return <tr>{side === "left" ? cells : cells.reverse()}</tr>;
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
