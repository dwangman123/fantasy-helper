"use client";

import { useEffect, useRef, useState } from "react";

import { byOwner } from "@/components/TeamSelect";
import type { Team } from "@/lib/api";
import { useDefaultTeam } from "@/lib/defaultTeam";

// Asks a first-time visitor which team is theirs, so the pages open on it. The top bar button
// shows the pick and opens the dialog again to change or clear it.
export function TeamChooser({ teams }: { teams: Team[] }) {
  const pick = useDefaultTeam();
  const [reopened, setReopened] = useState(false);
  const show = reopened || (pick.ready && !pick.chosen);
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (show && !d.open) d.showModal();
    else if (!show && d.open) d.close();
  }, [show]);

  const current = teams.find((t) => t.team_id === pick.teamId);
  const choose = (teamId: number | null) => {
    pick.set(teamId);
    setReopened(false);
  };
  // Esc, or the dialog closing any other way: a first visit counts as skipped, so it doesn't come back.
  const onClose = () => {
    if (!pick.chosen) pick.set(null);
    setReopened(false);
  };

  return (
    <>
      <button type="button" className="team-pick" onClick={() => setReopened(true)}>
        {current ? `My team: ${current.owner}` : "Pick my team"}
      </button>
      <dialog ref={dialog} className="team-chooser" aria-labelledby="team-chooser-title" onClose={onClose}>
        <h2 id="team-chooser-title">Which team is yours?</h2>
        <p className="sub">
          Each page opens on it. Any team can still be picked on every page, and this can be changed from the top
          bar.
        </p>
        <div className="team-chooser-grid">
          {byOwner(teams).map((t) => (
            <button
              key={t.team_id}
              type="button"
              aria-pressed={t.team_id === current?.team_id}
              onClick={() => choose(t.team_id)}
            >
              <strong>{t.owner}</strong>
              <span className="sub">
                {t.name !== t.owner && `${t.name} · `}
                {t.wins}-{t.losses}
                {t.ties ? `-${t.ties}` : ""}
              </span>
            </button>
          ))}
        </div>
        <div className="team-chooser-foot">
          {pick.chosen ? (
            <>
              {current && (
                <button type="button" onClick={() => choose(null)}>
                  Clear my team
                </button>
              )}
              <button type="button" onClick={() => setReopened(false)}>
                Close
              </button>
            </>
          ) : (
            <button type="button" onClick={() => choose(null)}>
              Skip, I&apos;m just looking
            </button>
          )}
        </div>
      </dialog>
    </>
  );
}
