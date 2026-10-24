import { pct } from "@/lib/format";

type Props = { win: number; tie: number; loss: number; height?: number; labels?: [string, string, string] };

// Win / tie / loss as one 100% bar: blue for my side, red for the opponent's, gray between.
export function OddsBar({ win, tie, loss, height = 8, labels = ["Win", "Tie", "Loss"] }: Props) {
  const parts = [
    { p: win, color: "var(--win)", label: labels[0] },
    { p: tie, color: "var(--tie)", label: labels[1] },
    { p: loss, color: "var(--loss)", label: labels[2] },
  ].filter((x) => x.p > 0.0005);
  const summary = parts.map((x) => `${x.label} ${pct(x.p)}`).join(", ");
  return (
    <div className="odds-bar" role="img" aria-label={summary} title={summary}>
      {parts.map((x) => (
        <span key={x.label} style={{ flexGrow: x.p, flexBasis: 0, height, background: x.color }} />
      ))}
    </div>
  );
}

export function OddsLegend({ labels = ["Win", "Tie", "Loss"] }: { labels?: [string, string, string] }) {
  return (
    <div className="legend">
      <span>
        <span className="key" style={{ background: "var(--win)" }} />
        {labels[0]}
      </span>
      <span>
        <span className="key" style={{ background: "var(--tie)" }} />
        {labels[1]}
      </span>
      <span>
        <span className="key" style={{ background: "var(--loss)" }} />
        {labels[2]}
      </span>
    </div>
  );
}
