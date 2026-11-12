import { OddsBar } from "@/components/OddsBar";
import type { CategoryOdds, Team } from "@/lib/api";
import { pct, stat } from "@/lib/format";

type Props = { rows: CategoryOdds[]; left: Team; right: Team; final: boolean };

// Each category head to head: the left team's totals so far and projected on its side, the
// right team's mirrored on the other, and the win / tie / loss odds between them, with each
// side's chance to win either side of its status. A finished week shows ESPN's final totals
// and who won each category.
export function CategoryH2H({ rows, left, right, final }: Props) {
  return (
    <div className="table-wrap">
      <table className="h2h">
        <thead>
          <tr>
            <th colSpan={final ? 1 : 2} className="l">
              <span className="key" style={{ background: "var(--win)" }} />
              {left.owner}
            </th>
            <th className="mid">Category</th>
            <th colSpan={final ? 1 : 2} className="r">
              {right.owner}
              <span className="key" style={{ background: "var(--loss)", margin: "0 0 0 6px" }} />
            </th>
          </tr>
          <tr>
            {!final && <th className="l">Current</th>}
            <th className="l">{final ? "Final" : "Projected"}</th>
            <th className="mid">{final ? "Result" : "Chance to win"}</th>
            <th className="r">{final ? "Final" : "Projected"}</th>
            {!final && <th className="r">Current</th>}
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
                  </div>
                  <OddsBar win={c.p_win} tie={c.p_tie} loss={c.p_loss} labels={[left.owner, "Tie", right.owner]} />
                  <div className="h2h-odds">
                    <span>{final ? "" : pct(c.p_win)}</span>
                    <Status odds={c} left={left} right={right} final={final} />
                    <span>{final ? "" : pct(c.p_loss)}</span>
                  </div>
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

// A finished week's result; otherwise locked for one side (over 90% to win it) or a swing
// category, with the chance of a tie when there is one.
function Status({ odds, left, right, final }: { odds: CategoryOdds; left: Team; right: Team; final: boolean }) {
  const [text, color] =
    odds.result === "win"
      ? [`${left.owner} won`, "var(--win)"]
      : odds.result === "loss"
        ? [`${right.owner} won`, "var(--loss)"]
        : odds.result === "tie"
          ? ["Tied", "var(--tie)"]
          : odds.label === "locked"
            ? [`${left.owner} locked`, "var(--win)"]
            : odds.label === "lost"
              ? [`${right.owner} locked`, "var(--loss)"]
              : ["Swing", "var(--muted)"];
  const tie = !final && odds.p_tie >= 0.005 ? ` · tie ${pct(odds.p_tie)}` : "";
  return (
    <span className="chip">
      <span className="dot" style={{ background: color }} />
      {text}
      {tie}
    </span>
  );
}
