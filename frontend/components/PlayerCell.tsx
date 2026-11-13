import type { Player } from "@/lib/api";
import { shortDate } from "@/lib/format";

const INJURY: Record<string, { text: string; color: string }> = {
  DAY_TO_DAY: { text: "Day-to-day", color: "var(--warning)" },
  OUT: { text: "Out", color: "var(--critical)" },
  SUSPENSION: { text: "Suspended", color: "var(--critical)" },
  INJURY_RESERVE: { text: "IR", color: "var(--critical)" },
};

export function InjuryBadge({ player }: { player: Player }) {
  const status = player.injury_status;
  if (!status || status === "ACTIVE") return null;
  const s = INJURY[status] ?? { text: status, color: "var(--warning)" };
  // Day-to-day returns are days away, so only a longer absence shows its date.
  const back = player.out_for_season
    ? "out for season"
    : player.return_date && status !== "DAY_TO_DAY"
      ? `back ~${shortDate(player.return_date)}`
      : null;
  const expected = player.return_date && !player.out_for_season ? `ESPN's expected return ${shortDate(player.return_date)}` : null;
  const title = [player.injury_note, expected].filter(Boolean).join(" · ");
  return (
    <span className="badge" title={title || undefined}>
      <span aria-hidden>⚠</span>
      <span className="dot" style={{ background: s.color }} />
      {back ? `${s.text} · ${back}` : s.text}
    </span>
  );
}

export function PlayerCell({ player, empty = "Open roster spot" }: { player: Player | null; empty?: string }) {
  if (!player) return <span className="player-meta">{empty}</span>;
  return (
    <div>
      <span className="player-name">{player.name}</span>
      <InjuryBadge player={player} />
      <div className="player-meta">
        {player.pro_team} · {player.position}
      </div>
    </div>
  );
}
