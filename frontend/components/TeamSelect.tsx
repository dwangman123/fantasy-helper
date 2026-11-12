"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";

import type { Team } from "@/lib/api";

// The team being viewed lives in the URL (?team=7), defaulting to mine.
export function useTeamParam(myTeamId: number | undefined): [number | undefined, (id: number) => void] {
  const params = useSearchParams();
  const router = useRouter();
  const path = usePathname();
  const fromUrl = Number(params.get("team"));
  const teamId = Number.isInteger(fromUrl) && fromUrl > 0 ? fromUrl : myTeamId;
  const setTeam = (id: number) => {
    const next = new URLSearchParams(params);
    if (id === myTeamId) next.delete("team");
    else next.set("team", String(id));
    router.replace(next.size ? `${path}?${next}` : path, { scroll: false });
  };
  return [teamId, setTeam];
}

type Props = { teams: Team[]; value: number; myTeamId: number; onChange: (id: number) => void };

export function TeamSelect({ teams, value, myTeamId, onChange }: Props) {
  return (
    <label className="toggle">
      Team
      <select value={value} onChange={(e) => onChange(Number(e.target.value))}>
        {teams.map((t) => (
          <option key={t.team_id} value={t.team_id}>
            {t.owner}
            {t.team_id === myTeamId ? " (mine)" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}
