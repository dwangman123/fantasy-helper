import type { Player } from "@/lib/api";

const INJURY: Record<string, { text: string; color: string }> = {
  DAY_TO_DAY: { text: "Day-to-day", color: "var(--warning)" },
  OUT: { text: "Out", color: "var(--critical)" },
  SUSPENSION: { text: "Suspended", color: "var(--critical)" },
  INJURY_RESERVE: { text: "IR", color: "var(--critical)" },
};

export function InjuryBadge({ status }: { status: string | null }) {
  if (!status || status === "ACTIVE") return null;
  const s = INJURY[status] ?? { text: status, color: "var(--warning)" };
  return (
    <span className="badge">
      <span aria-hidden>⚠</span>
      <span className="dot" style={{ background: s.color }} />
      {s.text}
    </span>
  );
}

export function PlayerCell({ player, empty = "Open roster spot" }: { player: Player | null; empty?: string }) {
  if (!player) return <span className="player-meta">{empty}</span>;
  return (
    <div>
      <span className="player-name">{player.name}</span>
      <InjuryBadge status={player.injury_status} />
      <div className="player-meta">
        {player.pro_team} · {player.position}
      </div>
    </div>
  );
}
