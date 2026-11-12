"use client";

import { Suspense, useState } from "react";

import { InjuryBadge } from "@/components/PlayerCell";
import { ErrorNotice, Loading } from "@/components/Status";
import { TeamSelect, useTeamParam } from "@/components/TeamSelect";
import { ZLegend, zClass } from "@/components/ZShade";
import {
  type Category,
  type CategoryProfile,
  type Move,
  type Stats,
  type TeamPlayer,
  useLeague,
  useTeam,
} from "@/lib/api";
import { PARTS, WINDOW_LABELS, dayOf, stat } from "@/lib/format";

export default function TeamPage() {
  return (
    <Suspense fallback={<Loading what="team" />}>
      <TeamView />
    </Suspense>
  );
}

function TeamView() {
  const league = useLeague();
  const [teamId, setTeam] = useTeamParam(league.data?.my_team_id);
  const [statWindow, setStatWindow] = useState("model");
  const detail = useTeam(teamId, statWindow);
  const error = league.error ?? detail.error;
  if (error) return <ErrorNotice error={error} />;
  if (!league.data || !detail.data || teamId === undefined) return <Loading what="team" />;

  const d = detail.data;
  const { team } = d;
  const categories = league.data.categories;
  return (
    <div className={`stack${detail.isPlaceholderData ? " fetching" : ""}`}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <h1>{team.owner}</h1>
          <div className="sub">
            {team.wins}-{team.losses}
            {team.ties ? `-${team.ties}` : ""} · {d.punts.length ? `punting ${d.punts.join(", ")}` : "no clear punts"}
          </div>
        </div>
        <div className="row">
          <TeamSelect teams={league.data.teams} value={teamId} myTeamId={league.data.my_team_id} onChange={setTeam} />
          <label className="toggle">
            Stats
            <select value={statWindow} onChange={(e) => setStatWindow(e.target.value)}>
              {d.windows.map((w) => (
                <option key={w} value={w}>
                  {WINDOW_LABELS[w] ?? w}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>Categories</h2>
            <div className="sub">Team per-game averages, ranked against the league</div>
          </div>
          <div className="legend" style={{ alignItems: "center", gap: 8 }}>
            <span>best</span>
            <span className="rank-scale" aria-hidden />
            <span>worst in the league</span>
          </div>
        </div>
        <div className="board">
          {d.profile.map((p) => (
            <ProfileTile
              key={p.key}
              p={p}
              teams={league.data.teams.length}
              isPercentage={categories.find((c) => c.key === p.key)?.is_percentage ?? false}
            />
          ))}
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>Roster</h2>
            <div className="sub">Per-game averages · {WINDOW_LABELS[d.window] ?? d.window}</div>
          </div>
          <ZLegend />
        </div>
        <RosterTable players={d.players} profile={d.profile} categories={categories} />
      </section>

      <div className="grid-2">
        <section className="card">
          <div className="card-head">
            <h2>Recent moves</h2>
            <span className="sub">last two weeks of adds and drops, plus trades</span>
          </div>
          <MoveList moves={d.moves} />
        </section>
        <section className="card">
          <h2 style={{ marginBottom: 12 }}>How to read this</h2>
          <ul className="assumptions">
            {d.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}

// A diverging tint by rank: deepest blue for the best team, fading to none mid-table, then red
// down to the worst. 38% matches the strongest z-score shade (.z-hi2 / .z-lo2).
function rankTint(rank: number, teams: number): string | undefined {
  const mid = (teams + 1) / 2;
  const t = teams > 1 ? (mid - rank) / (mid - 1) : 0;
  if (t === 0) return undefined;
  return `color-mix(in srgb, var(${t > 0 ? "--win" : "--loss"}) ${(Math.abs(t) * 38).toFixed(1)}%, transparent)`;
}

type TileProps = { p: CategoryProfile; teams: number; isPercentage: boolean };

function ProfileTile({ p, teams, isPercentage }: TileProps) {
  return (
    <div className="board-tile" style={{ background: rankTint(p.rank, teams) }}>
      <div className="cat">{p.key}</div>
      <div className="tile-value">{isPercentage ? stat(p.value, true) : p.value.toFixed(1)}</div>
      <span className="sub">
        rank {p.rank} of {teams}
      </span>
    </div>
  );
}

// A player's average in a category; FG% and FT% from his makes and attempts.
function perGame(stats: Stats, key: string): string {
  const parts = PARTS[key];
  if (!parts) return stats[key].toFixed(1);
  return stats[parts[1]] ? stat(stats[parts[0]] / stats[parts[1]], true) : "–";
}

type RosterProps = { players: TeamPlayer[]; profile: CategoryProfile[]; categories: Category[] };

function RosterTable({ players, profile, categories }: RosterProps) {
  const team = Object.fromEntries(profile.map((p) => [p.key, p]));
  return (
    <div className="table-wrap">
      <table className="roster">
        <thead>
          <tr>
            <th>Slot</th>
            <th>Player</th>
            <th className="num hide-sm">GP</th>
            <th className="num hide-sm">MIN</th>
            {categories.map((c) => (
              <th key={c.key} className="num">
                {c.key}
              </th>
            ))}
            <th className="num" title="G-score: z-scores summed, each weighted by how predictable its category is week to week">
              Value
            </th>
          </tr>
        </thead>
        <tbody>
          {players.map((p) => (
            <tr key={p.player.player_id}>
              <td className="sub">{p.slot}</td>
              <td>
                <span className="player-name">{p.player.name}</span>
                <InjuryBadge status={p.player.injury_status} />
                <div className="player-meta">
                  {p.player.pro_team} · {p.player.position}
                  {acquired(p)}
                </div>
                {p.specialties.length > 0 && (
                  <div className="specialties" title="Categories he stands out in">
                    {p.specialties.map((k) => (
                      <span key={k} className="chip specialty">
                        {k}
                      </span>
                    ))}
                  </div>
                )}
              </td>
              <td className="num hide-sm">{p.gp ?? "–"}</td>
              <td className="num hide-sm">{p.mpg ? p.mpg.toFixed(1) : "–"}</td>
              {categories.map((c) => (
                <td key={c.key} className={`num zcell ${zClass(p.z[c.key])}`}>
                  {p.per_game ? perGame(p.per_game, c.key) : "–"}
                </td>
              ))}
              <td className="num">
                <strong>{p.per_game ? p.value.toFixed(1) : "–"}</strong>
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td />
            <td>
              Team per game
              <div className="sub">IR excluded</div>
            </td>
            <td className="hide-sm" />
            <td className="hide-sm" />
            {categories.map((c) => (
              <td key={c.key} className="num">
                {team[c.key] ? (c.is_percentage ? stat(team[c.key].value, true) : team[c.key].value.toFixed(1)) : "–"}
              </td>
            ))}
            <td />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function acquired(p: TeamPlayer): string {
  if (!p.acquired_at || p.acquisition_type === "DRAFT") return p.acquisition_type === "DRAFT" ? " · drafted" : "";
  const how = p.acquisition_type === "TRADE" ? "traded for" : "added";
  return ` · ${how} ${dayOf(p.acquired_at)}`;
}

const MOVE_LABELS: Record<Move["type"], [string, string]> = {
  ADD: ["Added", "var(--win)"],
  DROP: ["Dropped", "var(--loss)"],
  TRADE: ["Traded for", "var(--muted)"],
};

function MoveList({ moves }: { moves: Move[] }) {
  if (moves.length === 0) return <div className="sub">No moves in the last two weeks.</div>;
  return (
    <table>
      <tbody>
        {moves.map((m) => {
          const [label, color] = MOVE_LABELS[m.type];
          return (
            <tr key={`${m.type}-${m.player_id}-${m.at}`}>
              <td className="sub" style={{ whiteSpace: "nowrap" }}>
                {m.at ? dayOf(m.at) : "–"}
              </td>
              <td>
                <span className="chip">
                  <span className="dot" style={{ background: color }} />
                  {label}
                </span>
              </td>
              <td>
                <span className="player-name">{m.player_name ?? `Player ${m.player_id}`}</span>
                <div className="player-meta">{m.kind === "WAIVER" ? "waivers" : m.kind === "FREEAGENT" ? "free agency" : "trade"}</div>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
