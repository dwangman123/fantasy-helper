"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { DeltaBar } from "@/components/DeltaBar";
import { Effects } from "@/components/Effects";
import { InjuryBadge } from "@/components/PlayerCell";
import { ErrorNotice, Loading } from "@/components/Status";
import { TeamSelect, useTeamParam } from "@/components/TeamSelect";
import {
  type Category,
  type Player,
  type Team,
  type TeamPlayer,
  type Trade,
  type TradeIdea,
  type TradeSide,
  type Trades,
  useLeague,
  useTeam,
  useTrade,
  useTrades,
} from "@/lib/api";
import { pct, pts, signed } from "@/lib/format";

const MAX_PER_SIDE = 3;

export default function TradesPage() {
  return (
    <Suspense fallback={<Loading what="trades" />}>
      <TradesView />
    </Suspense>
  );
}

const ids = (raw: string | null) => (raw ? raw.split(",").map(Number).filter((n) => Number.isInteger(n) && n > 0) : []);

// The deal being evaluated lives in the URL next to the team: ?partner=7&give=1,2&get=3.
function useDeal(myTeamId: number | undefined) {
  const params = useSearchParams();
  const router = useRouter();
  const path = usePathname();
  const [teamId] = useTeamParam(myTeamId);
  const partner = Number(params.get("partner"));
  const deal = {
    partnerId: Number.isInteger(partner) && partner > 0 ? partner : undefined,
    give: ids(params.get("give")),
    receive: ids(params.get("get")),
  };
  const update = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v === null || v === "") next.delete(k);
      else next.set(k, v);
    }
    router.replace(next.size ? `${path}?${next}` : path, { scroll: false });
  };
  const setDeal = (partnerId: number | undefined, give: number[], receive: number[]) =>
    update({ partner: partnerId ? String(partnerId) : null, give: give.join(","), get: receive.join(",") });
  // A different team has a different roster, so its deal starts over.
  const setTeam = (id: number) =>
    update({ team: id === myTeamId ? null : String(id), partner: null, give: null, get: null });
  return { teamId, ...deal, setDeal, setTeam };
}

function TradesView() {
  const league = useLeague();
  const { teamId, partnerId, give, receive, setDeal, setTeam } = useDeal(league.data?.my_team_id);
  const trades = useTrades(teamId);
  const error = league.error ?? trades.error;
  if (error) return <ErrorNotice error={error} />;
  if (!league.data || teamId === undefined) return <Loading what="trades" />;

  const teams = Object.fromEntries(league.data.teams.map((t) => [t.team_id, t]));
  const t = trades.data;
  const open = (d: TradeIdea) => {
    setDeal(
      d.partner.team_id,
      d.me.sends.map((p) => p.player_id),
      d.me.receives.map((p) => p.player_id),
    );
    document.getElementById("evaluator")?.scrollIntoView({ behavior: "smooth" });
  };
  return (
    <div className={`stack${trades.isPlaceholderData ? " fetching" : ""}`}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <h1>Trades for {teams[teamId]?.name}</h1>
          {t && (
            <div className="sub">
              Rest of season, weeks {t.first_period}–{t.first_period + t.weeks - 1}, against a league-average team
            </div>
          )}
        </div>
        <TeamSelect teams={league.data.teams} value={teamId} myTeamId={league.data.my_team_id} onChange={setTeam} />
      </div>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>Deals that help both teams</h2>
            <div className="sub">
              {t
                ? `${t.evaluated} of ${t.candidates.toLocaleString()} 1-for-1 and 2-for-1 deals scored in full`
                : "Searching every 1-for-1 and 2-for-1 deal takes a few seconds"}
            </div>
          </div>
        </div>
        {t ? <Finder trades={t} teams={teams} onOpen={open} /> : <Loading what="trade ideas" />}
      </section>

      <Evaluator
        teamId={teamId}
        partnerId={partnerId}
        give={give}
        receive={receive}
        teams={league.data.teams}
        categories={league.data.categories}
        onChange={setDeal}
      />

      {t && (
        <section className="card">
          <h2 style={{ marginBottom: 12 }}>How to read this</h2>
          <ul className="assumptions">
            <li>
              Cats / week is the change in categories won per week against a league-average team; win a week is the
              change in the chance of winning more categories than that team. A player&apos;s value is his G-score, the
              same as on the Team page: his z-scores summed, each weighted by how predictable that category is week to
              week, so steals and the percentages count for less.
            </li>
            {t.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

const SHOWN = 10;

function Finder({ trades, teams, onOpen }: { trades: Trades; teams: Record<number, Team>; onOpen: (d: TradeIdea) => void }) {
  const [partner, setPartner] = useState<number | null>(null);
  const [all, setAll] = useState(false);
  const deals = trades.deals.filter((d) => partner === null || d.partner.team_id === partner);
  const shown = all ? deals : deals.slice(0, SHOWN);
  const max = Math.max(...trades.deals.map((d) => Math.abs(d.me.d_cats_per_week)), 0.001);
  const partners = [...new Set(trades.deals.map((d) => d.partner.team_id))].sort((a, b) =>
    teams[a].name.localeCompare(teams[b].name),
  );
  if (!trades.deals.length) return <p className="sub">No deal found that helps both teams.</p>;
  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <label className="toggle">
          With
          <select
            value={partner ?? ""}
            onChange={(e) => {
              setPartner(e.target.value ? Number(e.target.value) : null);
              setAll(false);
            }}
          >
            <option value="">every team</option>
            {partners.map((id) => (
              <option key={id} value={id}>
                {teams[id].name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>With</th>
              <th>You give</th>
              <th>You get</th>
              <th className="num" title="your change in categories won per week">
                You
              </th>
              <th className="num" title="their change in categories won per week">
                Them
              </th>
              <th className="num hide-sm" title="your change in the chance to win a week">
                Win a week
              </th>
              <th className="hide-sm">Biggest effects</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {shown.map((d, i) => (
              <tr key={`${d.partner.team_id}-${d.me.sends.map((p) => p.player_id)}-${d.me.receives.map((p) => p.player_id)}`}>
                <td className="sub">{i + 1}</td>
                <td>{teams[d.partner.team_id]?.name}</td>
                <td>
                  <Players players={d.me.sends} />
                  <Moves side={d.me} />
                </td>
                <td>
                  <Players players={d.me.receives} />
                </td>
                <td className="num">
                  <DeltaBar value={d.me.d_cats_per_week} max={max} text={signed(d.me.d_cats_per_week)} />
                </td>
                <td className="num">{signed(d.partner.d_cats_per_week)}</td>
                <td className="num hide-sm">{pts(d.me.d_p_week)}</td>
                <td className="hide-sm">
                  <Effects deltas={d.me.cat_d_p_win} />
                </td>
                <td>
                  <button type="button" onClick={() => onOpen(d)}>
                    Open
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {deals.length > SHOWN && (
          <button type="button" className="more" onClick={() => setAll(!all)}>
            {all ? `Show top ${SHOWN}` : `Show all ${deals.length}`}
          </button>
        )}
      </div>
    </>
  );
}

function Players({ players }: { players: Player[] }) {
  return (
    <div className="deal-players">
      {players.map((p) => (
        <div key={p.player_id}>
          <span className="player-name">{p.name}</span>
          <InjuryBadge status={p.injury_status} />
          <span className="player-meta" title="G-score value">
            {" "}
            {p.pro_team} · value {p.value.toFixed(1)}
          </span>
        </div>
      ))}
    </div>
  );
}

// The roster moves a deal forces on one side: a drop to make room, or a pickup for an open spot.
function Moves({ side }: { side: TradeSide }) {
  const parts = [
    ...side.adds.map((p) => `picks up ${p.name}`),
    ...side.drops.map((p) => `drops ${p.name}`),
  ];
  if (!parts.length) return null;
  return <div className="player-meta">then {parts.join(", ")}</div>;
}

type EvaluatorProps = {
  teamId: number;
  partnerId: number | undefined;
  give: number[];
  receive: number[];
  teams: Team[];
  categories: Category[];
  onChange: (partnerId: number | undefined, give: number[], receive: number[]) => void;
};

function Evaluator({ teamId, partnerId, give, receive, teams, categories, onChange }: EvaluatorProps) {
  const mine = useTeam(teamId, "model");
  const theirs = useTeam(partnerId, "model");
  const trade = useTrade(teamId, partnerId, give, receive);
  const names = Object.fromEntries(teams.map((t) => [t.team_id, t.name]));
  const toggle = (list: number[], id: number) =>
    list.includes(id) ? list.filter((x) => x !== id) : list.length < MAX_PER_SIDE ? [...list, id] : list;
  return (
    <section className="card" id="evaluator">
      <div className="card-head">
        <div>
          <h2>Evaluate a trade</h2>
          <div className="sub">Pick up to {MAX_PER_SIDE} players on each side</div>
        </div>
        <label className="toggle">
          With
          <select
            value={partnerId ?? ""}
            onChange={(e) => onChange(e.target.value ? Number(e.target.value) : undefined, give, [])}
          >
            <option value="">choose a team</option>
            {teams
              .filter((t) => t.team_id !== teamId)
              .map((t) => (
                <option key={t.team_id} value={t.team_id}>
                  {t.name}
                </option>
              ))}
          </select>
        </label>
      </div>
      <div className="grid-2">
        <RosterPicker
          title="You send"
          players={mine.data?.players}
          selected={give}
          onToggle={(id) => onChange(partnerId, toggle(give, id), receive)}
        />
        {partnerId === undefined ? (
          <p className="sub">Choose a team to trade with, or open one of the deals above.</p>
        ) : (
          <RosterPicker
            title={`${names[partnerId] ?? "They"} send`}
            players={theirs.data?.players}
            selected={receive}
            onToggle={(id) => onChange(partnerId, give, toggle(receive, id))}
          />
        )}
      </div>
      {trade.error ? (
        <ErrorNotice error={trade.error} />
      ) : trade.data && give.length && receive.length ? (
        <div className={trade.isPlaceholderData ? "fetching" : undefined}>
          <TradeResult trade={trade.data} categories={categories} partnerName={names[partnerId ?? 0] ?? "They"} />
        </div>
      ) : (
        give.length > 0 && receive.length > 0 && <Loading what="the trade" />
      )}
    </section>
  );
}

type PickerProps = { title: string; players: TeamPlayer[] | undefined; selected: number[]; onToggle: (id: number) => void };

function RosterPicker({ title, players, selected, onToggle }: PickerProps) {
  if (!players) return <Loading what="roster" />;
  const full = selected.length >= MAX_PER_SIDE;
  return (
    <div>
      <h3 style={{ marginBottom: 8 }}>{title}</h3>
      <table>
        <tbody>
          {players
            .filter((p) => p.slot !== "IR")
            .map((p) => {
              const checked = selected.includes(p.player.player_id);
              return (
                <tr
                  key={p.player.player_id}
                  className={`clickable${checked ? " selected" : ""}`}
                  onClick={() => (checked || !full) && onToggle(p.player.player_id)}
                >
                  <td style={{ width: 28 }}>
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!checked && full}
                      onChange={() => onToggle(p.player.player_id)}
                      onClick={(e) => e.stopPropagation()}
                      aria-label={`Trade ${p.player.name}`}
                    />
                  </td>
                  <td>
                    <span className="player-name">{p.player.name}</span>
                    <InjuryBadge status={p.player.injury_status} />
                    <div className="player-meta">
                      {p.player.pro_team} · {p.player.position}
                    </div>
                  </td>
                  <td className="num" title="G-score value">
                    {p.value.toFixed(1)}
                  </td>
                </tr>
              );
            })}
        </tbody>
      </table>
    </div>
  );
}

// A change in a category's expected weekly value: "+5.4" for counting stats, "+.004" for percentages.
function valueChange(d: number, isPercentage: boolean): string {
  if (!isPercentage) return signed(d, 1);
  return `${d >= 0 ? "+" : "−"}${Math.abs(d).toFixed(3).replace(/^0/, "")}`;
}

function TradeResult({ trade, categories, partnerName }: { trade: Trade; categories: Category[]; partnerName: string }) {
  const sides: [string, TradeSide][] = [
    ["You", trade.me],
    [partnerName, trade.partner],
  ];
  const max = Math.max(...categories.flatMap((c) => sides.map(([, s]) => Math.abs(s.cat_d_p_win[c.key]))), 0.001);
  return (
    <div className="stack" style={{ marginTop: 16 }}>
      {trade.warnings.length > 0 && (
        <div className="notice">
          <ul className="assumptions">
            {trade.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="grid-2">
        {sides.map(([name, s]) => (
          <div key={s.team_id} className="trade-side">
            <div className="tile-label">{name}</div>
            <div className="tile-value">{signed(s.d_cats_per_week)}</div>
            <div className="sub">
              categories won per week (now {s.cats_per_week.toFixed(2)}) · win a week {pts(s.d_p_week)} (now{" "}
              {pct(s.p_week)})
            </div>
            <Moves side={s} />
          </div>
        ))}
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Category</th>
              {sides.map(([name, s]) => (
                <th key={s.team_id} className="num" colSpan={2}>
                  {name}: P(win) · per week
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {categories.map((c) => (
              <tr key={c.key}>
                <td>
                  <strong>{c.key}</strong>
                </td>
                {sides.map(([, s]) => (
                  <TradeCells key={s.team_id} side={s} category={c} max={max} />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TradeCells({ side, category, max }: { side: TradeSide; category: Category; max: number }) {
  const d = side.cat_d_p_win[category.key];
  return (
    <>
      <td className="num" title={`now ${pct(side.cat_p_win[category.key])} to win ${category.key} in a week`}>
        <DeltaBar value={d} max={max} text={pts(d)} />
      </td>
      <td className="num sub">{valueChange(side.cat_d_value[category.key], category.is_percentage)}</td>
    </>
  );
}
