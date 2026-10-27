"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";

import type { MatchupSummary, Team } from "@/lib/api";

// A matchup's left and right teams: mine on the left when I'm in it, otherwise home on the left.
export function sides(m: MatchupSummary, myTeamId: number): [number, number] {
  return m.away_team_id === myTeamId ? [m.away_team_id, m.home_team_id] : [m.home_team_id, m.away_team_id];
}

// The matchup being viewed lives in the URL (?matchup=12), defaulting to mine.
export function useMatchupParam(myMatchupId: number | undefined): [number | undefined, (id: number) => void] {
  const params = useSearchParams();
  const router = useRouter();
  const path = usePathname();
  const fromUrl = Number(params.get("matchup"));
  const matchupId = Number.isInteger(fromUrl) && fromUrl > 0 ? fromUrl : myMatchupId;
  const setMatchup = (id: number) => {
    const next = new URLSearchParams(params);
    if (id === myMatchupId) next.delete("matchup");
    else next.set("matchup", String(id));
    router.replace(next.size ? `${path}?${next}` : path, { scroll: false });
  };
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
