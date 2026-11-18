import { OddsBar } from "@/components/OddsBar";
import type { CategoryOdds, Team } from "@/lib/api";
import { pct, stat } from "@/lib/format";

type Props = { rows: CategoryOdds[]; left: Team; right: Team; final: boolean };

type Side = "left" | "right" | null;

// The side ahead on two totals (fewer is better for reversed categories); null when they're level
// or one is missing (a percentage before its first attempt).
function ahead(l: number | null, r: number | null, reverse: boolean): Side {
  if (l === null || r === null || l === r) return null;
  return (reverse ? l < r : l > r) ? "left" : "right";
}

// The score if the week ended now: the categories each side leads on its totals so far, the
// left side first, then the right, then ties.
export function currentScore(rows: CategoryOdds[]): { me: number; opp: number; ties: number } {
  const leads = rows.map((c) => ahead(c.now_me, c.now_opp, c.reverse));
  const me = leads.filter((x) => x === "left").length;
  const opp = leads.filter((x) => x === "right").length;
  return { me, opp, ties: rows.length - me - opp };
}

// Each category head to head: the left team's totals so far and projected on its side, the
// right team's mirrored on the other, and the win / tie / loss odds between them, with each
// side's chance to win either side of its status. A finished week shows ESPN's final totals
// and who won each category. Real totals (so far, or final) are shaded in the color of the side
// leading them; a projection only goes bold, so what has happened reads apart from what's expected.
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
            const now = ahead(c.now_me, c.now_opp, c.reverse);
            const projected = ahead(c.proj_me, c.proj_opp, c.reverse);
            const won: Side = c.result === "win" ? "left" : c.result === "loss" ? "right" : null;
            return (
              <tr key={c.key}>
                {!final && (
                  <td className="l now">
                    <Actual x={c.now_me} other={c.now_opp} isPercentage={c.is_percentage} side="left" lead={now} />
                  </td>
                )}
                <td className={`l final${projected === "left" ? " lead" : ""}`}>
                  {final ? (
                    <Actual x={c.proj_me} other={c.proj_opp} isPercentage={c.is_percentage} side="left" lead={won} />
                  ) : (
                    value(c.proj_me, c.proj_opp, c.is_percentage)
                  )}
                </td>
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
                <td className={`r final${projected === "right" ? " lead" : ""}`}>
                  {final ? (
                    <Actual x={c.proj_opp} other={c.proj_me} isPercentage={c.is_percentage} side="right" lead={won} />
                  ) : (
                    value(c.proj_opp, c.proj_me, c.is_percentage)
                  )}
                </td>
                {!final && (
                  <td className="r now">
                    <Actual x={c.now_opp} other={c.now_me} isPercentage={c.is_percentage} side="right" lead={now} />
                  </td>
                )}
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

type ActualProps = { x: number | null; other: number | null; isPercentage: boolean; side: "left" | "right"; lead: Side };

// A real total, shaded in its side's color when that side leads the category.
function Actual({ x, other, isPercentage, side, lead }: ActualProps) {
  const leads = lead === side;
  return (
    <span className={`actual${leads ? ` lead-${side}` : ""}`}>
      {x === null ? stat(null, isPercentage) : value(x, other ?? x, isPercentage)}
      {leads && <span className="sr-only"> (ahead)</span>}
    </span>
  );
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
