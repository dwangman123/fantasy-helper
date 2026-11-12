// The categories a move changes most, as chips: "STL +12". Flipped categories
// (now in my favor, or no longer) lead and are bold.
type Props = { deltas: Record<string, number>; gained?: string[]; lost?: string[]; threshold?: number; limit?: number };

export function Effects({ deltas, gained = [], lost = [], threshold = 0.01, limit = 4 }: Props) {
  const flipped = new Set([...gained, ...lost]);
  const keys = Object.keys(deltas)
    .filter((k) => flipped.has(k) || Math.abs(deltas[k]) >= threshold)
    .sort((a, b) => Number(flipped.has(b)) - Number(flipped.has(a)) || Math.abs(deltas[b]) - Math.abs(deltas[a]))
    .slice(0, limit);
  if (!keys.length) return <span className="player-meta">–</span>;
  return (
    <div className="effects">
      {keys.map((k) => {
        const d = deltas[k];
        const title = gained.includes(k)
          ? `${k} turns in the team's favor`
          : lost.includes(k)
            ? `${k} stops being in the team's favor`
            : `${k} P(win) ${d >= 0 ? "+" : "−"}${Math.abs(d * 100).toFixed(1)} pts`;
        return (
          <span key={k} className={`chip${flipped.has(k) ? " strong" : ""}`} title={title}>
            <span className="dot" style={{ background: d >= 0 ? "var(--win)" : "var(--loss)" }} />
            {k} {d >= 0 ? "+" : "−"}
            {Math.abs(Math.round(d * 100))}
            {gained.includes(k) && " ✓"}
            {lost.includes(k) && " ✗"}
          </span>
        );
      })}
    </div>
  );
}
