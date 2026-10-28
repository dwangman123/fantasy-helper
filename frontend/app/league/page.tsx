"use client";

import Link from "next/link";
import { useState } from "react";

import { ErrorNotice, Loading } from "@/components/Status";
import { ZLegend, zClass } from "@/components/ZShade";
import {
  type Category,
  type CategoryProfile,
  type Power,
  type TeamProfile,
  type WinLoss,
  useLeague,
  usePower,
  useProfiles,
} from "@/lib/api";
import { WINDOW_LABELS, pct, stat } from "@/lib/format";

export default function LeaguePage() {
  const league = useLeague();
  const power = usePower();
  const [statWindow, setStatWindow] = useState("model");
  const profiles = useProfiles(statWindow);
  const error = league.error ?? power.error ?? profiles.error;
  if (error) return <ErrorNotice error={error} />;
  if (!league.data) return <Loading what="league" />;

  const { my_team_id: myTeamId, categories } = league.data;
  const p = power.data;
  const last = p ? p.first_period + p.weeks - 1 : null;
  return (
    <div className="stack">
      <div>
        <h1>{league.data.name}</h1>
        <div className="sub">
          Week {league.data.week.matchup_period} · {p ? `${p.completed_weeks} completed` : "…"}
        </div>
      </div>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>Power rankings</h2>
            <div className="sub">
              {p ? `Rest-of-season strength against every team, weeks ${p.first_period}–${last}` : "Rest-of-season strength"}
            </div>
          </div>
        </div>
        {p ? <PowerTable power={p} myTeamId={myTeamId} /> : <Loading what="power rankings" />}
      </section>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>Categories</h2>
            <div className="sub">Team per-game values, shaded against the league&apos;s teams</div>
          </div>
          <div className="row">
            <ZLegend />
            <label className="toggle">
              Stats
              <select value={statWindow} onChange={(e) => setStatWindow(e.target.value)}>
                {(profiles.data?.windows ?? ["model"]).map((w) => (
                  <option key={w} value={w}>
                    {WINDOW_LABELS[w] ?? w}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>
        {profiles.data ? (
          <div className={profiles.isPlaceholderData ? "fetching" : undefined}>
            <Heatmap
              teams={profiles.data.teams}
              categories={categories}
              order={p?.rows.map((r) => r.team.team_id)}
              myTeamId={myTeamId}
            />
          </div>
        ) : (
          <Loading what="category profiles" />
        )}
      </section>

      <section className="card">
        <h2 style={{ marginBottom: 12 }}>How to read this</h2>
        <ul className="assumptions">
          {[...(p?.notes ?? []), ...(profiles.data?.notes ?? [])].map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      </section>
    </div>
  );
}

const record = (r: WinLoss) => `${r.wins}-${r.losses}${r.ties ? `-${r.ties}` : ""}`;

function TeamName({ id, name, myTeamId }: { id: number; name: string; myTeamId: number }) {
  return (
    <Link href={id === myTeamId ? "/team" : `/team?team=${id}`} className="player-name team-link">
      {name}
      {id === myTeamId && <span className="player-meta"> (mine)</span>}
    </Link>
  );
}

function PowerTable({ power, myTeamId }: { power: Power; myTeamId: number }) {
  const played = power.completed_weeks > 0;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Team</th>
            <th className="num" title="expected share of weekly matchups won against every other team">
              Strength
            </th>
            <th className="num hide-sm" title="expected categories won per week, averaged over the other teams">
              Cats / week
            </th>
            <th className="num" title="each completed week against every other team">
              All-play so far
            </th>
            <th className="hide-sm">Punts</th>
          </tr>
        </thead>
        <tbody>
          {power.rows.map((r) => (
            <tr key={r.team.team_id} className={r.team.team_id === myTeamId ? "selected" : undefined}>
              <td className="sub">{r.rank}</td>
              <td>
                <TeamName id={r.team.team_id} name={r.team.name} myTeamId={myTeamId} />
                <div className="player-meta">
                  {r.team.abbrev} · {record(r.team)}
                </div>
              </td>
              <td className="num">
                <div className="meter-cell">
                  <span>{pct(r.strength)}</span>
                  <span className="meter hide-sm" aria-hidden>
                    <span style={{ width: `${r.strength * 100}%` }} />
                  </span>
                </div>
              </td>
              <td className="num hide-sm">{r.cats_per_week.toFixed(2)}</td>
              <td className="num">
                {played ? (
                  <>
                    {record(r.all_play)}
                    <div className="player-meta">{pct(allPlayShare(r.all_play))}</div>
                  </>
                ) : (
                  "–"
                )}
              </td>
              <td className="hide-sm">
                {r.punts.length ? (
                  <div className="effects">
                    {r.punts.map((k) => (
                      <span key={k} className="chip">
                        <span className="dot" style={{ background: "var(--loss)" }} />
                        {k}
                      </span>
                    ))}
                  </div>
                ) : (
                  <span className="player-meta">–</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const allPlayShare = (r: WinLoss) => {
  const games = r.wins + r.losses + r.ties;
  return games ? (r.wins + r.ties / 2) / games : 0;
};

type HeatmapProps = { teams: TeamProfile[]; categories: Category[]; order?: number[]; myTeamId: number };

function Heatmap({ teams, categories, order, myTeamId }: HeatmapProps) {
  const rank = (id: number) => order?.indexOf(id) ?? -1;
  const rows = [...teams].sort((a, b) =>
    order ? rank(a.team.team_id) - rank(b.team.team_id) : a.team.name.localeCompare(b.team.name),
  );
  return (
    <div className="table-wrap">
      <table className="heatmap">
        <thead>
          <tr>
            <th>Team</th>
            {categories.map((c) => (
              <th key={c.key} className="num">
                {c.key}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((t) => {
            const byKey = Object.fromEntries(t.profile.map((p) => [p.key, p]));
            return (
              <tr key={t.team.team_id} className={t.team.team_id === myTeamId ? "selected" : undefined}>
                <td>
                  <TeamName id={t.team.team_id} name={t.team.name} myTeamId={myTeamId} />
                </td>
                {categories.map((c) => (
                  <HeatCell key={c.key} team={t.team.name} p={byKey[c.key]} category={c} teams={teams.length} />
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

type CellProps = { team: string; p: CategoryProfile | undefined; category: Category; teams: number };

function HeatCell({ team, p, category, teams }: CellProps) {
  if (!p) return <td className="num">–</td>;
  const value = category.is_percentage ? stat(p.value, true) : p.value.toFixed(1);
  const z = `${p.z >= 0 ? "+" : "−"}${Math.abs(p.z).toFixed(1)}`;
  const title = `${team} · ${category.key} ${value} per game · rank ${p.rank} of ${teams} · ${z} SD vs the league${
    p.punt ? " · punt" : ""
  }`;
  return (
    <td className={`num zcell ${zClass(p.z)}${p.punt ? " punt" : ""}`} title={title}>
      <div>{value}</div>
      <div className="player-meta">
        #{p.rank}
        {p.punt && " · punt"}
      </div>
    </td>
  );
}
