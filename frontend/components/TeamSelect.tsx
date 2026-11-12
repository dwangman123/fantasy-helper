"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";

import type { Team } from "@/lib/api";

// Teams in picker order: by owner.
export const byOwner = (teams: Team[]) => [...teams].sort((a, b) => a.owner.localeCompare(b.owner));

// The team being viewed lives in the URL (?team=7). Anyone in the league may be looking, so
// there's no "my team": without the param it's the first team by owner.
export function useTeamParam(teams: Team[] | undefined): [number | undefined, (id: number) => void] {
  const params = useSearchParams();
  const router = useRouter();
  const path = usePathname();
  const fromUrl = Number(params.get("team"));
  const fallback = teams?.length ? byOwner(teams)[0].team_id : undefined;
  const valid = Number.isInteger(fromUrl) && fromUrl > 0 && (!teams || teams.some((t) => t.team_id === fromUrl));
  const teamId = valid ? fromUrl : fallback;
  const setTeam = (id: number) => {
    const next = new URLSearchParams(params);
    next.set("team", String(id));
    router.replace(`${path}?${next}`, { scroll: false });
  };
  return [teamId, setTeam];
}

type Props = { teams: Team[]; value: number; onChange: (id: number) => void };

export function TeamSelect({ teams, value, onChange }: Props) {
  return (
    <label className="toggle">
      Team
      <select value={value} onChange={(e) => onChange(Number(e.target.value))}>
        {byOwner(teams).map((t) => (
          <option key={t.team_id} value={t.team_id}>
            {t.owner}
          </option>
        ))}
      </select>
    </label>
  );
}
