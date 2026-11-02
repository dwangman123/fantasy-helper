"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";

import type { MatchupSummary, Team, WeekSpan } from "@/lib/api";
import { shortDate } from "@/lib/format";

// A matchup's left and right teams: mine on the left when I'm in it, otherwise home on the left.
export function sides(m: MatchupSummary, myTeamId: number): [number, number] {
  return m.away_team_id === myTeamId ? [m.away_team_id, m.home_team_id] : [m.home_team_id, m.away_team_id];
}

// The page's URL params, and a way to edit them in place.
function useParams(): [URLSearchParams, (edit: (next: URLSearchParams) => void) => void] {
  const params = useSearchParams();
  const router = useRouter();
  const path = usePathname();
  const update = (edit: (next: URLSearchParams) => void) => {
    const next = new URLSearchParams(params);
    edit(next);
    router.replace(next.size ? `${path}?${next}` : path, { scroll: false });
  };
  return [params, update];
}

// The week being viewed lives in the URL (?week=2), defaulting to the current one. Changing
// it drops ?matchup=, whose id belongs to one week, so the new week opens on mine.
export function useWeekParam(current: number | undefined): [number | undefined, (week: number) => void] {
  const [params, update] = useParams();
  const fromUrl = Number(params.get("week"));
  const valid = current !== undefined && Number.isInteger(fromUrl) && fromUrl > 0 && fromUrl <= current;
  const setWeek = (week: number) =>
    update((next) => {
      next.delete("matchup");
      if (week === current) next.delete("week");
      else next.set("week", String(week));
    });
  return [valid ? fromUrl : current, setWeek];
}

// The matchup being viewed lives in the URL (?matchup=12), defaulting to mine.
export function useMatchupParam(myMatchupId: number | undefined): [number | undefined, (id: number) => void] {
  const [params, update] = useParams();
  const fromUrl = Number(params.get("matchup"));
  const matchupId = Number.isInteger(fromUrl) && fromUrl > 0 ? fromUrl : myMatchupId;
  const setMatchup = (id: number) =>
    update((next) => {
      if (id === myMatchupId) next.delete("matchup");
      else next.set("matchup", String(id));
    });
  return [matchupId, setMatchup];
}

type Props = {
  rows: MatchupSummary[];
  teams: Record<number, Team>;
  value: number;
  myTeamId: number;
  onChange: (id: number) => void;
};

export function MatchupSelect({ rows, teams, value, myTeamId, onChange }: Props) {
  return (
    <label className="toggle">
      Matchup
      <select value={value} onChange={(e) => onChange(Number(e.target.value))}>
        {rows.map((r) => {
          const [left, right] = sides(r, myTeamId);
          return (
            <option key={r.espn_id} value={r.espn_id}>
              {teams[left]?.name} vs {teams[right]?.name}
              {left === myTeamId ? " (mine)" : ""}
            </option>
          );
        })}
      </select>
    </label>
  );
}

// This week first, then back through the season.
export function WeekSelect({ weeks, value, onChange }: { weeks: WeekSpan[]; value: number; onChange: (week: number) => void }) {
  const current = weeks[weeks.length - 1]?.matchup_period;
  return (
    <label className="toggle">
      Week
      <select value={value} onChange={(e) => onChange(Number(e.target.value))}>
        {[...weeks].reverse().map((w) => (
          <option key={w.matchup_period} value={w.matchup_period}>
            Week {w.matchup_period} · {shortDate(w.start)} – {shortDate(w.end)}
            {w.matchup_period === current ? " (this week)" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}
