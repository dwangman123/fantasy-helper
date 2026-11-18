"use client";

import { useSyncExternalStore } from "react";

import type { Team } from "@/lib/api";

// The team a visitor picked on their first visit, kept in this browser. Pages open on it where
// they have a team to choose, and nothing marks it as "mine": it's only the option selected first.
const KEY = "fh-team";
const SKIPPED = "none"; // stored when the visitor chose not to pick one
const CHANGED = "fh-team-change"; // tells this tab's components; the storage event tells other tabs
const UNREAD = "unread"; // the server's and hydration's value, before localStorage can be read

let memory: string | null = null; // where the pick lives when localStorage is blocked

function read(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return memory;
  }
}

function write(value: string) {
  memory = value;
  try {
    localStorage.setItem(KEY, value);
  } catch {
    // blocked: the pick lasts until the page is reloaded
  }
  window.dispatchEvent(new Event(CHANGED));
}

function subscribe(onChange: () => void) {
  const onStorage = (e: StorageEvent) => {
    if (e.key === KEY || e.key === null) onChange();
  };
  window.addEventListener(CHANGED, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(CHANGED, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

export type DefaultTeam = {
  ready: boolean; // the stored pick has been read (not on the server, nor while hydrating)
  chosen: boolean; // a team was picked, or picking was skipped
  teamId: number | null; // the picked team, if any
  set: (teamId: number | null) => void; // null: no default team
};

export function useDefaultTeam(): DefaultTeam {
  const raw = useSyncExternalStore(subscribe, read, () => UNREAD);
  const id = Number(raw);
  return {
    ready: raw !== UNREAD,
    chosen: raw !== UNREAD && raw !== null,
    teamId: Number.isInteger(id) && id > 0 ? id : null,
    set: (teamId) => write(teamId === null ? SKIPPED : String(teamId)),
  };
}

// The picked team if it's one of `teams`, else null; undefined until both are known, so a page
// doesn't start loading some other team first.
export function usePickedTeam(teams: Team[] | undefined): number | null | undefined {
  const { ready, teamId } = useDefaultTeam();
  if (!ready || !teams) return undefined;
  return teams.some((t) => t.team_id === teamId) ? teamId : null;
}
