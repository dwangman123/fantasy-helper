import type { Stats } from "@/lib/api";

export const pct = (p: number) => `${Math.round(p * 100)}%`;

// A change in probability, in percentage points: "+4.1 pts".
export const pts = (d: number) => `${d >= 0 ? "+" : "−"}${Math.abs(d * 100).toFixed(1)} pts`;

export const signed = (x: number, digits = 2) => `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(digits)}`;

// A category total: FG% as .472, counting stats as whole numbers.
export const stat = (value: number | null | undefined, isPercentage: boolean) => {
  if (value === null || value === undefined) return "–";
  return isPercentage ? value.toFixed(3).replace(/^0/, "") : Math.round(value).toLocaleString();
};

// Dates come from the API as YYYY-MM-DD; parse as local days, not UTC midnight.
const day = (iso: string) => {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
};

export const shortDate = (iso: string) => day(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });

export const weekday = (iso: string) =>
  day(iso).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });

export const dateTime = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });

// The local day of a timestamp, e.g. "Oct 21".
export const dayOf = (iso: string) => new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });

export const timeOfDay = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

// Percentage categories come from their makes and attempts.
export const PARTS: Record<string, [string, string]> = { "FG%": ["FGM", "FGA"], "FT%": ["FTM", "FTA"] };

// Box scores are whole numbers; projections and averages keep a decimal.
export const num = (x: number, exact: boolean) => (exact ? Math.round(x).toString() : x.toFixed(1));

// One game's line in a category: makes/attempts for FG% and FT%.
export function lineValue(stats: Stats, key: string, exact: boolean): string {
  const parts = PARTS[key];
  return parts ? `${num(stats[parts[0]], exact)}/${num(stats[parts[1]], exact)}` : num(stats[key], exact);
}

// A total in a category: a percentage from the summed makes and attempts.
export function totalValue(stats: Stats, key: string, exact: boolean): string {
  const parts = PARTS[key];
  if (!parts) return num(stats[key], exact);
  return stats[parts[1]] ? stat(stats[parts[0]] / stats[parts[1]], true) : "–";
}

// Stat windows the Team and League pages can show; "model" is what predictions use.
export const WINDOW_LABELS: Record<string, string> = {
  model: "Model (season + projection)",
  season: "Season",
  last_7: "Last 7 days",
  last_15: "Last 15 days",
  last_30: "Last 30 days",
  projected: "Projected",
};
