"use client";

import { Suspense, useState } from "react";

import { DeltaBar } from "@/components/DeltaBar";
import { Effects } from "@/components/Effects";
import { LabelChip } from "@/components/LabelChip";
import { OddsBar, OddsLegend } from "@/components/OddsBar";
import { PlayerCell } from "@/components/PlayerCell";
import { ErrorNotice, Loading } from "@/components/Status";
import { TeamSelect, useTeamParam } from "@/components/TeamSelect";
import { type Waivers, useLeague, useWaivers } from "@/lib/api";
import { pct, pts, signed, weekday } from "@/lib/format";

export default function WaiversPage() {
  return (
    <Suspense fallback={<Loading what="waivers" />}>
      <WaiversView />
    </Suspense>
  );
}

function WaiversView() {
  const league = useLeague();
  const [teamId, setTeam] = useTeamParam(league.data?.my_team_id);
  const [sameDay, setSameDay] = useState(false);
  const waivers = useWaivers(teamId, sameDay);
  const error = league.error ?? waivers.error;
  if (error) return <ErrorNotice error={error} />;
  if (!league.data || teamId === undefined) return <Loading what="waivers" />;
  const teams = Object.fromEntries(league.data.teams.map((t) => [t.team_id, t]));
  const w = waivers.data;

  return (
    <div className={`stack${waivers.isPlaceholderData ? " fetching" : ""}`}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <h1>Waivers for {teams[teamId]?.name}</h1>
          {w && (
            <div className="sub">
              Week {w.matchup_period} vs {teams[w.opponent_id]?.name}: {pct(w.baseline.p_win)} to win as is · pickups
              play from {weekday(w.first_day)}
            </div>
          )}
        </div>
        <div className="row">
          <label className="toggle">
            <input type="checkbox" checked={sameDay} onChange={(e) => setSameDay(e.target.checked)} />
            Pickups can play today
          </label>
          <TeamSelect teams={league.data.teams} value={teamId} myTeamId={league.data.my_team_id} onChange={setTeam} />
        </div>
      </div>
      {w ? <Rankings w={w} /> : <Loading what="waiver rankings (simulating each swap takes a few seconds)" />}
    </div>
  );
}

const SHOWN = 10;

function Rankings({ w }: { w: Waivers }) {
  const [allStream, setAllStream] = useState(false);
  const [allHold, setAllHold] = useState(false);
  const stream = allStream ? w.stream : w.stream.slice(0, SHOWN);
  const hold = allHold ? w.hold.rows : w.hold.rows.slice(0, SHOWN);
  const streamMax = Math.max(...w.stream.map((r) => Math.abs(r.d_p_win)), 0.001);
  const holdMax = Math.max(...w.hold.rows.map((r) => Math.abs(r.d_cats_per_week)), 0.001);
  const drops = w.drops.map((d) => (d ? d.name : "an open roster spot")).join(", ");
  return (
    <>
      <section className="card">
        <div className="card-head">
          <h2>This week&apos;s categories</h2>
          <OddsLegend />
        </div>
        <div className="board">
          {w.board.map((c) => (
            <div key={c.key} className="board-tile">
              <div className="cat">
                <span>{c.key}</span>
                <span>{pct(c.p_win)}</span>
              </div>
              <OddsBar win={c.p_win} tie={c.p_tie} loss={c.p_loss} />
              <LabelChip label={c.label} />
            </div>
          ))}
        </div>
        <p className="sub" style={{ marginBottom: 0 }}>
          Locked: over 90% to win. Lost: under 10%. Streaming only pays off in swing categories, and hurting a lost
          one costs nothing.
        </p>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Stream: this week</h2>
          <span className="sub">
            change in your chance to win this matchup · {w.stream_screened} of {w.free_agents} free agents simulated
          </span>
        </div>
        {w.stream.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Add</th>
                  <th>Drop</th>
                  <th className="num hide-sm" title="his games from the first day he can play">
                    Games
                  </th>
                  <th className="num hide-sm" title="team starts gained after the daily lineup cap">
                    +Starts
                  </th>
                  <th className="num">Win odds</th>
                  <th className="num hide-sm" title="change in expected categories won">
                    Cats
                  </th>
                  <th className="hide-sm">Biggest effects</th>
                </tr>
              </thead>
              <tbody>
                {stream.map((r, i) => (
                  <tr key={r.add.player_id}>
                    <td className="sub">{i + 1}</td>
                    <td>
                      <PlayerCell player={r.add} />
                    </td>
                    <td>
                      <PlayerCell player={r.drop} />
                    </td>
                    <td className="num hide-sm">{r.games}</td>
                    <td className="num hide-sm">{signed(r.extra_starts, 1)}</td>
                    <td className="num">
                      <DeltaBar value={r.d_p_win} max={streamMax} text={pts(r.d_p_win)} />
                    </td>
                    <td className="num hide-sm">{signed(r.d_expected_cats)}</td>
                    <td className="hide-sm">
                      <Effects deltas={r.cat_d_p_win} gained={r.gained} lost={r.lost} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <MoreButton total={w.stream.length} all={allStream} onToggle={() => setAllStream(!allStream)} />
          </div>
        ) : (
          <p className="sub">No free agent has a game he could start in the days left.</p>
        )}
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Hold: rest of season</h2>
          <span className="sub">
            change in categories won per week vs a league-average team over the next {w.hold.weeks} weeks (now{" "}
            {w.hold.cats_per_week.toFixed(2)})
          </span>
        </div>
        {w.hold.rows.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Add</th>
                  <th>Drop</th>
                  <th className="num hide-sm">Games left</th>
                  <th className="num">Cats / week</th>
                  <th className="hide-sm">Biggest effects</th>
                </tr>
              </thead>
              <tbody>
                {hold.map((r, i) => (
                  <tr key={r.add.player_id}>
                    <td className="sub">{i + 1}</td>
                    <td>
                      <PlayerCell player={r.add} />
                    </td>
                    <td>
                      <PlayerCell player={r.drop} />
                    </td>
                    <td className="num hide-sm">{r.games}</td>
                    <td className="num">
                      <DeltaBar value={r.d_cats_per_week} max={holdMax} text={signed(r.d_cats_per_week)} />
                    </td>
                    <td className="hide-sm">
                      <Effects deltas={r.cat_d_p_win} threshold={0.005} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <MoreButton total={w.hold.rows.length} all={allHold} onToggle={() => setAllHold(!allHold)} />
          </div>
        ) : (
          <p className="sub">No weeks left after this one.</p>
        )}
      </section>

      <section className="card">
        <h2 style={{ marginBottom: 12 }}>How to read this</h2>
        <ul className="assumptions">
          <li>
            Drops considered: {drops} (your lowest-value players). Each free agent shows his best drop.
            &quot;Biggest effects&quot; is the change in your chance to win each category, in points; ✓ and ✗ mark
            categories that swing in or out of your favor.
          </li>
          {w.assumptions.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
      </section>
    </>
  );
}

function MoreButton({ total, all, onToggle }: { total: number; all: boolean; onToggle: () => void }) {
  if (total <= SHOWN) return null;
  return (
    <button type="button" className="more" onClick={onToggle}>
      {all ? `Show top ${SHOWN}` : `Show all ${total}`}
    </button>
  );
}
