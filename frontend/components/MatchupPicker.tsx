"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { OddsBar } from "@/components/OddsBar";
import type { MatchupSummary, Team, WeekSpan } from "@/lib/api";
import { pct, shortDate } from "@/lib/format";

// A matchup's left (blue) and right (red) teams: home on the left.
export function sides(m: MatchupSummary): [number, number] {
  return [m.home_team_id, m.away_team_id];
}

// The page's URL params, and a way to edit them in place.
function useParams(): [URLSearchParams, (edit: (next: URLSearchParams) => void) => void] {
  const params = useSearchParams();
  const router = useRouter();
  const path = usePathname();
  const update = (edit: (next: URLSearchParams) => void) => {
    const next = new URLSearchParams(params);
    edit(next);
    router.replace(next.size ? `${path}?${next}` : path, { scroll: false });
  };
  return [params, update];
}

// The week being viewed lives in the URL (?week=2), defaulting to the current one. Changing
// it drops ?matchup=, whose id belongs to one week, so the new week opens on its first matchup.
export function useWeekParam(current: number | undefined): [number | undefined, (week: number) => void] {
  const [params, update] = useParams();
  const fromUrl = Number(params.get("week"));
  const valid = current !== undefined && Number.isInteger(fromUrl) && fromUrl > 0 && fromUrl <= current;
  const setWeek = (week: number) =>
    update((next) => {
      next.delete("matchup");
      if (week === current) next.delete("week");
      else next.set("week", String(week));
    });
  return [valid ? fromUrl : current, setWeek];
}

// The matchup being viewed lives in the URL (?matchup=12), defaulting to the week's first.
export function useMatchupParam(firstMatchupId: number | undefined): [number | undefined, (id: number) => void] {
  const [params, update] = useParams();
  const fromUrl = Number(params.get("matchup"));
  const matchupId = Number.isInteger(fromUrl) && fromUrl > 0 ? fromUrl : firstMatchupId;
  const setMatchup = (id: number) =>
    update((next) => {
      if (id === firstMatchupId) next.delete("matchup");
      else next.set("matchup", String(id));
    });
  return [matchupId, setMatchup];
}

// One button per matchup in the week: each side's owner and chance to win, or, for a finished
// week, the categories each side won. Key squares tie each name to its end of the bar.
type StripProps = {
  rows: MatchupSummary[];
  teams: Record<number, Team>;
  categories: number;
  value: number;
  onChange: (id: number) => void;
};

export function MatchupStrip({ rows, teams, categories, value, onChange }: StripProps) {
  return (
    <div className="matchup-strip" role="group" aria-label="Matchup">
      {rows.map((r) => {
        const [leftId, rightId] = sides(r);
        const [winL, winR] = [r.p_home, r.p_away];
        const [catsL, catsR] = [r.expected_home, r.expected_away];
        // A finished week's odds are its result, so it shows (and shades) categories won instead.
        const [l, rt] = r.final ? [catsL, catsR] : [winL, winR];
        const tie = r.final ? Math.max(categories - catsL - catsR, 0) / categories : r.p_tie;
        const show = (x: number) => (r.final ? String(Math.round(x)) : pct(x));
        const left = teams[leftId]?.owner ?? "";
        const right = teams[rightId]?.owner ?? "";
        return (
          <button
            key={r.espn_id}
            type="button"
            className="matchup-card"
            aria-pressed={r.espn_id === value}
            onClick={() => onChange(r.espn_id)}
          >
            <span className="matchup-line">
              <span>
                <span className="key" style={{ background: "var(--win)" }} />
                {left}
              </span>
              <span className={l >= rt ? "lead" : "trail"}>{show(l)}</span>
            </span>
            <span className="matchup-line">
              <span>
                <span className="key" style={{ background: "var(--loss)" }} />
                {right}
              </span>
              <span className={rt > l ? "lead" : "trail"}>{show(rt)}</span>
            </span>
            <OddsBar
              win={r.final ? catsL / categories : winL}
              tie={tie}
              loss={r.final ? catsR / categories : winR}
              height={6}
              labels={[left, "Tie", right]}
            />
          </button>
        );
      })}
    </div>
  );
}

// Back and forward through the season's weeks, the current one last.
export function WeekStepper({ weeks, value, onChange }: { weeks: WeekSpan[]; value: number; onChange: (week: number) => void }) {
  const i = weeks.findIndex((w) => w.matchup_period === value);
  const last = weeks.length - 1;
  const week = weeks[i];
  return (
    <div className="week-stepper" role="group" aria-label="Week">
      <button
        type="button"
        className="icon-button"
        aria-label="Previous week"
        disabled={i <= 0}
        onClick={() => onChange(weeks[i - 1].matchup_period)}
      >
        <Chevron points="10 3 5 8 10 13" />
      </button>
      <div className="week-label" aria-live="polite">
        <div className="week-title">Week {value}</div>
        {week && (
          <div className="sub">
            {shortDate(week.start)} – {shortDate(week.end)}
            {i === last ? " · this week" : ""}
          </div>
        )}
      </div>
      <button
        type="button"
        className="icon-button"
        aria-label="Next week"
        disabled={i === -1 || i >= last}
        onClick={() => onChange(weeks[i + 1].matchup_period)}
      >
        <Chevron points="6 3 11 8 6 13" />
      </button>
    </div>
  );
}

function Chevron({ points }: { points: string }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden>
      <polyline
        points={points}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
