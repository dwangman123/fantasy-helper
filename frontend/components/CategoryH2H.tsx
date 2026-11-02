import { OddsBar } from "@/components/OddsBar";
import type { CategoryOdds, Team } from "@/lib/api";
import { pct, stat } from "@/lib/format";

type Props = { rows: CategoryOdds[]; left: Team; right: Team; final: boolean };

// Each category head to head: the left team's totals now and projected on its side, the
// right team's mirrored on the other, and the win / tie / loss odds between them. A
// finished week shows ESPN's final totals and who won each category.
export function CategoryH2H({ rows, left, right, final }: Props) {
  return (
    <div className="table-wrap">
      <table className="h2h">
        <thead>
          <tr>
            <th colSpan={final ? 1 : 2} className="l">
              <span className="key" style={{ background: "var(--win)" }} />
              {left.abbrev}
            </th>
            <th className="mid">Category</th>
            <th colSpan={final ? 1 : 2} className="r">
              {right.abbrev}
              <span className="key" style={{ background: "var(--loss)", margin: "0 0 0 6px" }} />
            </th>
          </tr>
          <tr>
            {!final && <th className="l">Now</th>}
            <th className="l">Final</th>
            <th className="mid">{final ? "Result" : `${left.abbrev} win · tie · ${right.abbrev} win`}</th>
            <th className="r">Final</th>
            {!final && <th className="r">Now</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => {
            // The side projected to finish ahead (fewer is better for reversed categories).
            const leftAhead = c.reverse ? c.proj_me < c.proj_opp : c.proj_me > c.proj_opp;
            const rightAhead = c.reverse ? c.proj_opp < c.proj_me : c.proj_opp > c.proj_me;
            return (
              <tr key={c.key}>
                {!final && <td className="l now">{stat(c.now_me, c.is_percentage)}</td>}
                <td className={`l final${leftAhead ? " lead" : ""}`}>{value(c.proj_me, c.proj_opp, c.is_percentage)}</td>
                <td className="mid">
                  <div className="h2h-cat">
                    <strong>{c.key}</strong>
                    {c.reverse && <span className="sub">fewer wins</span>}
                    <Status odds={c} left={left} right={right} />
                  </div>
                  <OddsBar win={c.p_win} tie={c.p_tie} loss={c.p_loss} labels={[left.abbrev, "Tie", right.abbrev]} />
                  {!final && (
                    <div className="h2h-odds">
                      <span>{pct(c.p_win)}</span>
                      <span>{c.p_tie >= 0.005 ? `tie ${pct(c.p_tie)}` : ""}</span>
                      <span>{pct(c.p_loss)}</span>
                    </div>
                  )}
                </td>
                <td className={`r final${rightAhead ? " lead" : ""}`}>{value(c.proj_opp, c.proj_me, c.is_percentage)}</td>
                {!final && <td className="r now">{stat(c.now_opp, c.is_percentage)}</td>}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// A total; a percentage to 4 places when 3 would show a tie that isn't one (.7764 vs .7760).
function value(x: number, other: number, isPercentage: boolean): string {
  if (isPercentage && x !== other && stat(x, true) === stat(other, true)) return x.toFixed(4).replace(/^0/, "");
  return stat(x, isPercentage);
}

// A finished week's result; otherwise locked for one side (over 90% to win it) or a swing category.
function Status({ odds, left, right }: { odds: CategoryOdds; left: Team; right: Team }) {
  const [text, color] =
    odds.result === "win"
      ? [`${left.abbrev} won`, "var(--win)"]
      : odds.result === "loss"
        ? [`${right.abbrev} won`, "var(--loss)"]
        : odds.result === "tie"
          ? ["tied", "var(--tie)"]
          : odds.label === "locked"
            ? [`${left.abbrev} locked`, "var(--win)"]
            : odds.label === "lost"
              ? [`${right.abbrev} locked`, "var(--loss)"]
              : ["swing", "var(--muted)"];
  return (
    <span className="chip">
      <span className="dot" style={{ background: color }} />
      {text}
    </span>
  );
}
